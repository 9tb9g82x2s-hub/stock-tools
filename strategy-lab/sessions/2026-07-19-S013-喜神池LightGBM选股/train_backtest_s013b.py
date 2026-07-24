#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S013b · 喜神+忌神4星扩展池
  = S009 v1.5(C1·12%止损) + 选股范围限定于"金水喜神池(1485只)"

核心改动（相对S009）：
  打分时 score_df 过滤为喜神池内的票，只在金水股里选Top20。
  训练仍用全市场数据（宽训练集保留更多信息），仅打分/选股环节限定喜神池。

其他全部沿用S009 v1.5：
  - 滚动12个月训练窗口，LightGBM(32特征)
  - 月度调仓，Top20等权，T+1开盘价买入
  - C1·-12%收盘价止损
  - 买入0.025% + 卖出0.025%+印花税0.05%

断点续跑：每完成12期保存一次checkpoint(ckpt_plus.json)，重启从上次断点继续。
"""
import json, sqlite3, time, os
import numpy as np, pandas as pd
import lightgbm as lgb

BASE_DIR = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S013-喜神池LightGBM选股"
S009_DIR = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股"
PANEL_PATH = f"{BASE_DIR}/features_panel.pkl"
DB_PATH = "/Users/ziruzhu/stock-data/stock_all.db"
XISHEN_PATH = f"{BASE_DIR}/xishen_plus_pool.csv"
CKPT_PATH = f"{BASE_DIR}/ckpt_plus.json"
LOG_PATH = f"{BASE_DIR}/s013b_run.log"

BACKTEST_START = "20170101"
TRAIN_MONTHS = 12
TOP_N = 20
STOP_LOSS = -0.12

BUY_COMMISSION = 0.00025
SELL_COMMISSION = 0.00025
STAMP_TAX = 0.0005

FEATURE_COLS = [
    "mom_5","mom_10","mom_20","mom_60","mom_120",
    "turnover_rate","turnover_rate_f","volume_ratio","vol_chg_20",
    "bias_5","bias_10","bias_20","bias_60",
    "macd_dif","macd_dea","macd","kdj_k","kdj_d","kdj_j",
    "rsi_6","rsi_12","rsi_24","cci","boll_pct","boll_width",
    "pe","pe_ttm","pb","ps","ps_ttm","dv_ttm",
    "net_mf_ratio","lg_buy_ratio",
]


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def load_blacklist():
    con = sqlite3.connect(DB_PATH)
    st = pd.read_sql("SELECT ts_code FROM blacklist_st", con)["ts_code"].tolist()
    loss = pd.read_sql("SELECT ts_code FROM blacklist_loss", con)["ts_code"].tolist()
    con.close()
    return set(st) | set(loss)


def get_month_start_dates(trade_dates):
    s = pd.Series(pd.to_datetime(trade_dates, format="%Y%m%d"))
    df = pd.DataFrame({"date": s, "trade_date": trade_dates})
    df["ym"] = df["date"].dt.to_period("M")
    return df.groupby("ym").first()["trade_date"].tolist()


def main():
    t0 = time.time()
    log("="*60)
    log("S013b · 喜神+忌神4星扩展池 回测启动")

    # 喜神池
    pool = pd.read_csv(XISHEN_PATH)
    xishen_set = set(pool["ts_code"])
    log(f"喜神池: {len(xishen_set)} 只 (金{(pool['主五行']=='金').sum()}/水{(pool['主五行']=='水').sum()})")

    log("读取特征面板...")
    panel = pd.read_pickle(PANEL_PATH)

    blacklist = load_blacklist()
    panel = panel[~panel["ts_code"].isin(blacklist)].reset_index(drop=True)
    panel = panel[~panel["ts_code"].str.endswith(".BJ")].reset_index(drop=True)
    panel = panel.dropna(subset=FEATURE_COLS, how="all")
    log(f"面板清洗后: {len(panel):,} 行")

    all_trade_dates = sorted(panel["trade_date"].unique())
    month_dates = get_month_start_dates(all_trade_dates)
    rebalance_dates = [d for d in month_dates if d >= BACKTEST_START]
    log(f"调仓日: {len(rebalance_dates)}, 首={rebalance_dates[0]}, 末={rebalance_dates[-1]}")

    panel_by_date = {d: sub for d, sub in panel.groupby("trade_date")}
    open_lookup = panel.set_index(["ts_code", "trade_date"])["open_qfq"].sort_index()
    close_lookup = panel.set_index(["ts_code", "trade_date"])["close_qfq"].sort_index()
    next_trade_date = {d: all_trade_dates[i+1] for i, d in enumerate(all_trade_dates) if i+1 < len(all_trade_dates)}

    # 断点续跑
    trades = []
    nav = 1.0
    nav_curve = []
    prev_holdings = set()
    total_stops, total_checks = 0, 0
    resume_from = 0

    if os.path.exists(CKPT_PATH):
        try:
            ckpt = json.load(open(CKPT_PATH))
            trades = ckpt["trades"]
            nav = ckpt["nav"]
            nav_curve = ckpt["nav_curve"]
            total_stops = ckpt["total_stops"]
            total_checks = ckpt["total_checks"]
            resume_from = ckpt["next_i"]
            prev_holdings = set(ckpt.get("prev_holdings", []))
            log(f"[断点续跑] 从第{resume_from}期继续 (已完成{len(trades)}期, nav={nav:.4f})")
        except Exception as e:
            log(f"[断点] 读取失败({e})，从头开始")
            resume_from = 0

    for i, rd in enumerate(rebalance_dates):
        if i < resume_from:
            continue

        rd_dt = pd.to_datetime(rd, format="%Y%m%d")
        train_start = (rd_dt - pd.DateOffset(months=TRAIN_MONTHS)).strftime("%Y%m%d")
        # 训练集：全市场（宽训练集，保留最多信息）
        train_mask = (panel["trade_date"] >= train_start) & (panel["trade_date"] < rd)
        train_df = panel.loc[train_mask].dropna(subset=FEATURE_COLS + ["label"])
        if len(train_df) < 5000:
            continue

        # 打分集：只限喜神池
        score_df_raw = panel_by_date.get(rd)
        if score_df_raw is None or len(score_df_raw) == 0:
            continue
        score_df = score_df_raw[score_df_raw["ts_code"].isin(xishen_set)].dropna(subset=FEATURE_COLS, how="all").copy()
        if len(score_df) < TOP_N:
            log(f"  跳过 {rd}: 喜神池当期可评分股票不足{TOP_N}只 (实有{len(score_df)}只)")
            continue

        model = lgb.LGBMClassifier(
            boosting_type="gbdt", num_leaves=31, learning_rate=0.05,
            n_estimators=200, subsample=0.8, colsample_bytree=0.8,
            random_state=42, verbose=-1)
        model.fit(train_df[FEATURE_COLS], train_df["label"])
        scores = model.predict_proba(score_df[FEATURE_COLS])[:, 1]
        score_df["score"] = scores
        top20 = score_df.sort_values("score", ascending=False).head(TOP_N)["ts_code"].tolist()

        next_rd = rebalance_dates[i+1] if i+1 < len(rebalance_dates) else None
        if next_rd is None:
            break
        buy_date = next_trade_date.get(rd)
        sell_date = next_trade_date.get(next_rd)
        if buy_date is None or sell_date is None:
            continue

        rets, valid_holdings = [], []
        stop_count = 0

        for code in top20:
            try:
                entry_px = open_lookup.loc[(code, buy_date)]
                if pd.isna(entry_px) or entry_px <= 0:
                    continue
                stop_hit = False
                stop_px = None
                d = buy_date
                while d < sell_date:
                    d_next = next_trade_date.get(d)
                    if d_next is None:
                        break
                    try:
                        cl = close_lookup.loc[(code, d_next)]
                    except KeyError:
                        d = d_next
                        continue
                    if not pd.isna(cl) and cl > 0 and cl / entry_px - 1 <= STOP_LOSS:
                        stop_hit = True
                        stop_px = cl
                        break
                    d = d_next

                if stop_hit:
                    r = float(stop_px) / float(entry_px) - 1
                    stop_count += 1
                else:
                    try:
                        exit_px = open_lookup.loc[(code, sell_date)]
                    except KeyError:
                        continue
                    if pd.isna(exit_px) or exit_px <= 0:
                        continue
                    r = float(exit_px) / float(entry_px) - 1

                rets.append(r)
                valid_holdings.append(code)
            except KeyError:
                continue

        total_stops += stop_count
        total_checks += len(valid_holdings)
        if len(rets) == 0:
            continue

        gross_ret = float(np.mean(rets))
        curr_holdings = set(valid_holdings)
        bought = curr_holdings - prev_holdings
        sold = prev_holdings - curr_holdings
        n_curr = len(curr_holdings) if curr_holdings else 1
        n_prev = len(prev_holdings) if prev_holdings else 1
        buy_turnover = len(bought) / n_curr
        sell_turnover = len(sold) / n_prev if n_prev > 0 else 0.0
        cost = buy_turnover * BUY_COMMISSION + sell_turnover * (SELL_COMMISSION + STAMP_TAX)
        net_ret = gross_ret - cost
        nav *= (1 + net_ret)
        nav_curve.append({"date": sell_date, "nav": round(nav, 6)})
        trades.append({
            "rebalance_date": rd, "buy_date": buy_date, "next_date": next_rd,
            "sell_date": sell_date, "holdings": valid_holdings, "n_holdings": len(valid_holdings),
            "gross_return": round(gross_ret, 6), "trading_cost": round(cost, 6),
            "period_return": round(net_ret, 6),
            "buy_turnover": round(buy_turnover, 4), "sell_turnover": round(sell_turnover, 4),
            "win_count": int(sum(1 for r in rets if r > 0)), "stop_count": stop_count
        })
        prev_holdings = curr_holdings

        if (len(trades)) % 12 == 0:
            log(f"已完成 {i+1}/{len(rebalance_dates)} 期 ({rd}), 净值:{nav:.4f}")
            # 保存断点
            json.dump({
                "trades": trades, "nav": nav, "nav_curve": nav_curve,
                "total_stops": total_stops, "total_checks": total_checks,
                "next_i": i + 1, "prev_holdings": list(prev_holdings)
            }, open(CKPT_PATH, "w"), ensure_ascii=False, default=str)

    log(f"共{len(trades)}期, 最终净值:{nav:.4f}")
    if total_checks > 0:
        log(f"止损统计: {total_stops}/{total_checks} 触发, 比率{total_stops/total_checks*100:.1f}%")

    period_rets = np.array([t["period_return"] for t in trades])
    n_periods = len(period_rets)
    nav_arr = np.array([1.0] + [c["nav"] for c in nav_curve])
    running_max = np.maximum.accumulate(nav_arr)
    dd = nav_arr / running_max - 1
    max_dd = float(dd.min())
    n_years = n_periods / 12.0 if n_periods > 0 else 1
    ann_ret = (nav ** (1 / n_years) - 1) if n_years > 0 and nav > 0 else 0.0
    win_rate = float(np.mean(period_rets > 0))
    sharpe = float(period_rets.mean() / period_rets.std() * np.sqrt(12)) if period_rets.std() > 0 else 0.0

    log(f"年化:{ann_ret*100:.2f}%  胜率:{win_rate*100:.1f}%  最大回撤:{max_dd*100:.2f}%  夏普:{sharpe:.2f}")
    log(f"=== 对比S009基线：年化40.97% 回撤-20.9% 夏普1.457 ===")
    log(f"耗时{time.time()-t0:.1f}s")

    result = {
        "strategy_name": "S013b 喜神+忌神4星池(3074只)",
        "xishen_pool_size": len(xishen_set),
        "metrics": {
            "total_return": round(float(nav - 1), 4),
            "annual_return": round(float(ann_ret), 4),
            "win_rate": round(win_rate, 4),
            "max_drawdown": round(max_dd, 4),
            "sharpe_ratio": round(sharpe, 4),
            "total_trades": n_periods,
        },
        "vs_s009": {"s009_annual": 0.4097, "s009_mdd": -0.209, "s009_sharpe": 1.457},
        "stop_loss": {"threshold": STOP_LOSS, "total_stops": total_stops, "total_checks": total_checks},
        "nav_curve": nav_curve,
        "trades": trades,
    }
    out = f"{BASE_DIR}/s013b_result.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    pd.DataFrame(trades).to_csv(f"{BASE_DIR}/trades_s013b.csv", index=False, encoding="utf-8-sig")
    log(f"结果已写出: {out}")
    # 清理断点文件（跑完了不需要了）
    if os.path.exists(CKPT_PATH):
        os.remove(CKPT_PATH)


if __name__ == "__main__":
    main()
