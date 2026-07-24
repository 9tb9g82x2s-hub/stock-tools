#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S013 长保质期模型实验
重新定义 label = 未来 HORIZON 天跑赢中位数，重训 LightGBM，
持仓期匹配 HORIZON（每 HOLD_MONTHS 个月调仓），检验"长模型+长持仓"是否更好。

对比基准：S013b 原版 = label未来10天 + 月度调仓 = 年化36.6% 回撤-14.3%

用法: python s013_long_horizon.py <HORIZON> <HOLD_MONTHS>
  例: python s013_long_horizon.py 40 2   # 40天label, 每2个月调仓
      python s013_long_horizon.py 60 3   # 60天label, 每3个月调仓

断点续跑: ckpt_h{HORIZON}.json
"""
import json, sqlite3, time, os, sys
import numpy as np, pandas as pd
import lightgbm as lgb

BASE_DIR = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S013-喜神池LightGBM选股"
OUT_DIR  = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S014-Top精选集中策略"
PANEL_PATH = f"{BASE_DIR}/features_panel.pkl"
DB_PATH = "/Users/ziruzhu/stock-data/stock_all.db"
XISHEN_PATH = f"{BASE_DIR}/xishen_plus_pool.csv"

HORIZON = int(sys.argv[1]) if len(sys.argv) > 1 else 40      # label窗口(交易日)
HOLD_MONTHS = int(sys.argv[2]) if len(sys.argv) > 2 else 2   # 每N个月调仓
SOURCE = sys.argv[3] if len(sys.argv) > 3 else "s013"        # s013(喜神池) 或 s009(全市场)

if SOURCE == "s009":
    PANEL_PATH = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股/features_panel.pkl"
    USE_XISHEN = False
else:
    USE_XISHEN = True

CKPT_PATH = f"{OUT_DIR}/ckpt_{SOURCE}_h{HORIZON}.json"
LOG_PATH = f"{OUT_DIR}/long_{SOURCE}_h{HORIZON}_run.log"

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
    log(f"长模型实验: SOURCE={SOURCE}, HORIZON={HORIZON}天, 每{HOLD_MONTHS}月调仓")

    if USE_XISHEN:
        pool = pd.read_csv(XISHEN_PATH)
        xishen_set = set(pool["ts_code"])
        log(f"喜神池: {len(xishen_set)} 只")
    else:
        xishen_set = None
        log("全市场打分(S009模式,不限喜神池)")

    log("读取特征面板...")
    panel = pd.read_pickle(PANEL_PATH)

    # ─── 重算 label = 未来 HORIZON 天跑赢中位数 ───
    log(f"重算label(未来{HORIZON}天)...")
    panel = panel.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    g = panel.groupby("ts_code", group_keys=False)
    panel["fwd_ret_new"] = g["close_qfq"].transform(lambda s: s.shift(-HORIZON) / s - 1)
    median_by_date = panel.groupby("trade_date")["fwd_ret_new"].transform("median")
    panel["label"] = (panel["fwd_ret_new"] > median_by_date).astype("Int8")
    panel.loc[panel["fwd_ret_new"].isna(), "label"] = pd.NA
    log(f"新label分布: {panel['label'].value_counts(dropna=False).to_dict()}")

    blacklist = load_blacklist()
    panel = panel[~panel["ts_code"].isin(blacklist)].reset_index(drop=True)
    panel = panel[~panel["ts_code"].str.endswith(".BJ")].reset_index(drop=True)
    panel = panel.dropna(subset=FEATURE_COLS, how="all")
    log(f"面板清洗后: {len(panel):,} 行")

    all_trade_dates = sorted(panel["trade_date"].unique())
    month_dates = get_month_start_dates(all_trade_dates)
    rebalance_all = [d for d in month_dates if d >= BACKTEST_START]
    # 每 HOLD_MONTHS 个月取一个调仓日
    rebalance_dates = rebalance_all[::HOLD_MONTHS]
    log(f"调仓日: {len(rebalance_dates)}个 (每{HOLD_MONTHS}月), 首={rebalance_dates[0]} 末={rebalance_dates[-1]}")

    panel_by_date = {d: sub for d, sub in panel.groupby("trade_date")}
    open_lookup = panel.set_index(["ts_code", "trade_date"])["open_qfq"].sort_index()
    close_lookup = panel.set_index(["ts_code", "trade_date"])["close_qfq"].sort_index()
    next_trade_date = {d: all_trade_dates[i+1] for i, d in enumerate(all_trade_dates) if i+1 < len(all_trade_dates)}

    trades, nav, nav_curve = [], 1.0, []
    prev_holdings = set()
    total_stops, total_checks = 0, 0
    resume_from = 0

    if os.path.exists(CKPT_PATH):
        try:
            ckpt = json.load(open(CKPT_PATH))
            trades = ckpt["trades"]; nav = ckpt["nav"]; nav_curve = ckpt["nav_curve"]
            total_stops = ckpt["total_stops"]; total_checks = ckpt["total_checks"]
            resume_from = ckpt["next_i"]; prev_holdings = set(ckpt.get("prev_holdings", []))
            log(f"[断点续跑] 从第{resume_from}期继续 (已{len(trades)}期, nav={nav:.4f})")
        except Exception as e:
            log(f"[断点]读取失败({e}),从头")
            resume_from = 0

    for i, rd in enumerate(rebalance_dates):
        if i < resume_from:
            continue
        rd_dt = pd.to_datetime(rd, format="%Y%m%d")
        train_start = (rd_dt - pd.DateOffset(months=TRAIN_MONTHS)).strftime("%Y%m%d")
        train_mask = (panel["trade_date"] >= train_start) & (panel["trade_date"] < rd)
        train_df = panel.loc[train_mask].dropna(subset=FEATURE_COLS + ["label"])
        if len(train_df) < 5000:
            continue

        score_df_raw = panel_by_date.get(rd)
        if score_df_raw is None or len(score_df_raw) == 0:
            continue
        if USE_XISHEN:
            score_df = score_df_raw[score_df_raw["ts_code"].isin(xishen_set)].dropna(subset=FEATURE_COLS, how="all").copy()
        else:
            score_df = score_df_raw.dropna(subset=FEATURE_COLS, how="all").copy()
        if len(score_df) < TOP_N:
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

        rets, valid_holdings, stop_count = [], [], 0
        for code in top20:
            try:
                entry_px = open_lookup.loc[(code, buy_date)]
                if pd.isna(entry_px) or entry_px <= 0:
                    continue
                stop_hit, stop_px, d = False, None, buy_date
                while d < sell_date:
                    d_next = next_trade_date.get(d)
                    if d_next is None:
                        break
                    try:
                        cl = close_lookup.loc[(code, d_next)]
                    except KeyError:
                        d = d_next; continue
                    if not pd.isna(cl) and cl > 0 and cl / entry_px - 1 <= STOP_LOSS:
                        stop_hit, stop_px = True, cl; break
                    d = d_next
                if stop_hit:
                    r = float(stop_px) / float(entry_px) - 1; stop_count += 1
                else:
                    try:
                        exit_px = open_lookup.loc[(code, sell_date)]
                    except KeyError:
                        continue
                    if pd.isna(exit_px) or exit_px <= 0:
                        continue
                    r = float(exit_px) / float(entry_px) - 1
                rets.append(r); valid_holdings.append(code)
            except KeyError:
                continue

        total_stops += stop_count; total_checks += len(valid_holdings)
        if len(rets) == 0:
            continue
        gross_ret = float(np.mean(rets))
        curr_holdings = set(valid_holdings)
        bought = curr_holdings - prev_holdings; sold = prev_holdings - curr_holdings
        n_curr = len(curr_holdings) if curr_holdings else 1
        n_prev = len(prev_holdings) if prev_holdings else 1
        buy_turnover = len(bought) / n_curr
        sell_turnover = len(sold) / n_prev if n_prev > 0 else 0.0
        cost = buy_turnover * BUY_COMMISSION + sell_turnover * (SELL_COMMISSION + STAMP_TAX)
        net_ret = gross_ret - cost
        nav *= (1 + net_ret)
        nav_curve.append({"date": sell_date, "nav": round(nav, 6)})
        trades.append({
            "rebalance_date": rd, "buy_date": buy_date, "sell_date": sell_date,
            "holdings": valid_holdings, "n_holdings": len(valid_holdings),
            "gross_return": round(gross_ret, 6), "trading_cost": round(cost, 6),
            "period_return": round(net_ret, 6),
            "win_count": int(sum(1 for r in rets if r > 0)), "stop_count": stop_count
        })
        prev_holdings = curr_holdings

        if len(trades) % 6 == 0:
            log(f"已完成 {i+1}/{len(rebalance_dates)} 期 ({rd}), nav:{nav:.4f}")
            json.dump({"trades": trades, "nav": nav, "nav_curve": nav_curve,
                       "total_stops": total_stops, "total_checks": total_checks,
                       "next_i": i + 1, "prev_holdings": list(prev_holdings)},
                      open(CKPT_PATH, "w"), ensure_ascii=False, default=str)

    # 汇总
    period_rets = np.array([t["period_return"] for t in trades])
    n_periods = len(period_rets)
    # 年数：用实际时间跨度
    first_dt = pd.to_datetime(trades[0]["buy_date"], format="%Y%m%d")
    last_dt = pd.to_datetime(trades[-1]["sell_date"], format="%Y%m%d")
    n_years = (last_dt - first_dt).days / 365.25
    nav_arr = np.array([1.0] + [c["nav"] for c in nav_curve])
    dd = (nav_arr / np.maximum.accumulate(nav_arr) - 1).min()
    ann_ret = nav ** (1 / n_years) - 1 if n_years > 0 and nav > 0 else 0.0
    win_rate = float(np.mean(period_rets > 0))
    # 夏普：按调仓频率年化
    periods_per_year = 12 / HOLD_MONTHS
    sharpe = float(period_rets.mean() / period_rets.std() * np.sqrt(periods_per_year)) if period_rets.std() > 0 else 0.0

    log(f"[结果] HORIZON={HORIZON} 每{HOLD_MONTHS}月: 期数{n_periods} 年化{ann_ret*100:.2f}% 回撤{dd*100:.2f}% 夏普{sharpe:.2f} 胜率{win_rate*100:.1f}%")
    log(f"[基准] S013b原版(10天/月度): 年化36.6% 回撤-14.3% 夏普1.44")

    result = {
        "config": {"horizon": HORIZON, "hold_months": HOLD_MONTHS, "top_n": TOP_N, "stop_loss": STOP_LOSS},
        "metrics": {"annual_return": round(float(ann_ret), 4), "max_drawdown": round(float(dd), 4),
                    "sharpe_ratio": round(sharpe, 4), "win_rate": round(win_rate, 4),
                    "total_return": round(float(nav - 1), 4), "n_periods": n_periods},
        "nav_curve": nav_curve, "trades": trades,
    }
    out = f"{OUT_DIR}/{SOURCE}_long_h{HORIZON}_result.json"
    json.dump(result, open(out, "w"), ensure_ascii=False, indent=2, default=str)
    log(f"结果已写出: {out}  耗时{time.time()-t0:.0f}s")
    if os.path.exists(CKPT_PATH):
        os.remove(CKPT_PATH)

if __name__ == "__main__":
    main()
