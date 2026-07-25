#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
5天版止损扫描：在单一进程内顺序跑7档止损
避免bash循环跨轮次被系统清理，断了看哪些文件生成了，没生成的续跑
"""
import json, sqlite3, time, os, sys
import numpy as np, pandas as pd
import lightgbm as lgb

BASE_DIR = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S013-喜神池LightGBM选股"
OUT_DIR  = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S014-Top精选集中策略"
PANEL_PATH = f"{BASE_DIR}/features_panel.pkl"
DB_PATH = "/Users/ziruzhu/stock-data/stock_all.db"
XISHEN_PATH = f"{BASE_DIR}/xishen_plus_pool.csv"

HORIZON = 5
HOLD_DAYS = 5
SOURCE = "s013"
TOP_N = 20
BUY_COMMISSION = 0.00025
SELL_COMMISSION = 0.00025
STAMP_TAX = 0.0005
BACKTEST_START = "20170101"
TRAIN_MONTHS = 12

# 7档止损：None=无止损，其余为负数
SL_CONFIGS = [None, -0.03, -0.05, -0.07, -0.10, -0.12, -0.15]

FEATURE_COLS = [
    "mom_5","mom_10","mom_20","mom_60","mom_120",
    "turnover_rate","turnover_rate_f","volume_ratio","vol_chg_20",
    "bias_5","bias_10","bias_20","bias_60",
    "macd_dif","macd_dea","macd","kdj_k","kdj_d","kdj_j",
    "rsi_6","rsi_12","rsi_24","cci","boll_pct","boll_width",
    "pe","pe_ttm","pb","ps","ps_ttm","dv_ttm",
    "net_mf_ratio","lg_buy_ratio",
]

LOG_PATH = f"/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-21-S017-喜神池20天月度策略/scan_stoploss.log"

def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")

def out_path(sl):
    if sl is None:
        return f"{OUT_DIR}/s013_scan_h5_d5_slNone_result.json"
    return f"{OUT_DIR}/s013_scan_h5_d5_sl{int(abs(sl)*100)}_result.json"

def load_blacklist():
    con = sqlite3.connect(DB_PATH)
    st = pd.read_sql("SELECT ts_code FROM blacklist_st", con)["ts_code"].tolist()
    loss = pd.read_sql("SELECT ts_code FROM blacklist_loss", con)["ts_code"].tolist()
    con.close()
    return set(st) | set(loss)

def run_one(panel, all_trade_dates, open_lookup, close_lookup,
            next_trade_date, xishen_set, rebalance_dates, stop_loss):
    sl_desc = "无止损" if stop_loss is None else f"{stop_loss*100:.0f}%"
    log(f"  开始 stop_loss={sl_desc}")
    t0 = time.time()
    panel_by_date = {d: sub for d, sub in panel.groupby("trade_date")}
    trades, nav, nav_curve = [], 1.0, []
    prev_holdings = set()
    total_stops = 0

    for i, rd in enumerate(rebalance_dates):
        rd_dt = pd.to_datetime(rd, format="%Y%m%d")
        train_start = (rd_dt - pd.DateOffset(months=TRAIN_MONTHS)).strftime("%Y%m%d")
        train_mask = (panel["trade_date"] >= train_start) & (panel["trade_date"] < rd)
        train_df = panel.loc[train_mask].dropna(subset=FEATURE_COLS + ["label"])
        if len(train_df) < 5000:
            continue
        score_df_raw = panel_by_date.get(rd)
        if score_df_raw is None or len(score_df_raw) == 0:
            continue
        score_df = score_df_raw[score_df_raw["ts_code"].isin(xishen_set)].dropna(subset=FEATURE_COLS, how="all").copy()
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
                if stop_loss is not None:
                    while d < sell_date:
                        d_next = next_trade_date.get(d)
                        if d_next is None:
                            break
                        try:
                            cl = close_lookup.loc[(code, d_next)]
                        except KeyError:
                            d = d_next; continue
                        if not pd.isna(cl) and cl > 0 and cl / entry_px - 1 <= stop_loss:
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

        total_stops += stop_count
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

    period_rets = np.array([t["period_return"] for t in trades])
    n_periods = len(period_rets)
    first_dt = pd.to_datetime(trades[0]["buy_date"], format="%Y%m%d")
    last_dt = pd.to_datetime(trades[-1]["sell_date"], format="%Y%m%d")
    n_years = (last_dt - first_dt).days / 365.25
    nav_arr = np.array([1.0] + [c["nav"] for c in nav_curve])
    dd = (nav_arr / np.maximum.accumulate(nav_arr) - 1).min()
    ann_ret = nav ** (1 / n_years) - 1 if n_years > 0 and nav > 0 else 0.0
    win_rate = float(np.mean(period_rets > 0))
    periods_per_year = 250 / HOLD_DAYS
    sharpe = float(period_rets.mean() / period_rets.std() * np.sqrt(periods_per_year)) if period_rets.std() > 0 else 0.0
    log(f"  完成 sl={sl_desc}: 年化{ann_ret*100:.2f}% 回撤{dd*100:.2f}% 夏普{sharpe:.2f} 耗时{time.time()-t0:.0f}s")
    return {
        "config": {"horizon": HORIZON, "hold_days": HOLD_DAYS, "stop_loss": stop_loss, "top_n": TOP_N},
        "metrics": {"annual_return": round(float(ann_ret), 4), "max_drawdown": round(float(dd), 4),
                    "sharpe_ratio": round(sharpe, 4), "win_rate": round(win_rate, 4),
                    "total_return": round(float(nav - 1), 4), "n_periods": n_periods,
                    "total_stops": total_stops},
        "nav_curve": nav_curve, "trades": trades,
    }

def main():
    log("="*60)
    log(f"止损扫描：{len(SL_CONFIGS)}档，5天版喜神池")
    log("加载公共数据...")
    pool = pd.read_csv(XISHEN_PATH)
    xishen_set = set(pool["ts_code"])
    panel = pd.read_pickle(PANEL_PATH)
    panel = panel.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    g = panel.groupby("ts_code", group_keys=False)
    panel["fwd_ret_new"] = g["close_qfq"].transform(lambda s: s.shift(-HORIZON) / s - 1)
    median_by_date = panel.groupby("trade_date")["fwd_ret_new"].transform("median")
    panel["label"] = (panel["fwd_ret_new"] > median_by_date).astype("Int8")
    panel.loc[panel["fwd_ret_new"].isna(), "label"] = pd.NA
    blacklist = load_blacklist()
    panel = panel[~panel["ts_code"].isin(blacklist)].reset_index(drop=True)
    panel = panel[~panel["ts_code"].str.endswith(".BJ")].reset_index(drop=True)
    panel = panel.dropna(subset=FEATURE_COLS, how="all")
    log(f"面板: {len(panel):,} 行，喜神池: {len(xishen_set)} 只")
    all_trade_dates = sorted(panel["trade_date"].unique())
    all_backtest_dates = [d for d in all_trade_dates if d >= BACKTEST_START]
    rebalance_dates = all_backtest_dates[::HOLD_DAYS]
    log(f"调仓日: {len(rebalance_dates)} 个，首={rebalance_dates[0]} 末={rebalance_dates[-1]}")
    open_lookup = panel.set_index(["ts_code", "trade_date"])["open_qfq"].sort_index()
    close_lookup = panel.set_index(["ts_code", "trade_date"])["close_qfq"].sort_index()
    next_trade_date = {d: all_trade_dates[i+1] for i, d in enumerate(all_trade_dates) if i+1 < len(all_trade_dates)}
    log("公共数据加载完成，开始逐档回测")

    for sl in SL_CONFIGS:
        p = out_path(sl)
        if os.path.exists(p):
            log(f"  跳过 sl={sl}（已有结果: {os.path.basename(p)}）")
            continue
        result = run_one(panel, all_trade_dates, open_lookup, close_lookup,
                         next_trade_date, xishen_set, rebalance_dates, sl)
        json.dump(result, open(p, "w"), ensure_ascii=False, indent=2, default=str)
        log(f"  已存: {os.path.basename(p)}")

    log("="*60)
    log("全部完成，汇总:")
    log(f"{'止损':>8} {'年化':>8} {'回撤':>9} {'夏普':>6} {'胜率':>7} {'总倍数':>8}")
    for sl in SL_CONFIGS:
        p = out_path(sl)
        if not os.path.exists(p):
            log(f"{'无止损' if sl is None else str(int(abs(sl)*100))+'%':>8} [未生成]"); continue
        m = json.load(open(p))["metrics"]
        sl_str = "无止损" if sl is None else f"-{int(abs(sl)*100)}%"
        log(f"{sl_str:>8} {m['annual_return']*100:>7.2f}% {m['max_drawdown']*100:>8.2f}% {m['sharpe_ratio']:>6.2f} {m['win_rate']*100:>6.1f}% {m['total_return']+1:>7.1f}x")

if __name__ == "__main__":
    main()
