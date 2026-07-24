#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S017 + 股价上限过滤版本
在每期打分时额外过滤 close_qfq > PRICE_CAP 的高价股
测试 PRICE_CAP = 100 / 150 / 200 / 300 / 无限制 对比
"""
import json, sqlite3, time, os, sys
import numpy as np, pandas as pd
import lightgbm as lgb

BASE_DIR = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S013-喜神池LightGBM选股"
OUT_DIR  = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S014-Top精选集中策略"
PANEL_PATH = f"{BASE_DIR}/features_panel.pkl"
DB_PATH = "/Users/ziruzhu/stock-data/stock_all.db"
XISHEN_PATH = f"{BASE_DIR}/xishen_plus_pool.csv"

# 参数
HORIZON = 20
HOLD_MONTHS = 1
PRICE_CAPS = [100, 150, 200, 300, 9999]  # 9999=不限制(原版S017)

BACKTEST_START = "20170101"
TRAIN_MONTHS = 12
TOP_N = 20
STOP_LOSS = -0.12
BC, SC = 0.00025, 0.00125

FEATURE_COLS = [
    "mom_5","mom_10","mom_20","mom_60","mom_120",
    "turnover_rate","turnover_rate_f","volume_ratio","vol_chg_20",
    "bias_5","bias_10","bias_20","bias_60",
    "macd_dif","macd_dea","macd","kdj_k","kdj_d","kdj_j",
    "rsi_6","rsi_12","rsi_24","cci","boll_pct","boll_width",
    "pe","pe_ttm","pb","ps","ps_ttm","dv_ttm",
    "net_mf_ratio","lg_buy_ratio",
]

def log(msg): print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

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

def run_backtest(panel, pool_set, rebalance_dates, open_lookup, close_lookup, next_trade_date, price_cap):
    trades, nav = [], 1.0
    prev_holdings = set()
    panel_by_date = {d: sub for d, sub in panel.groupby("trade_date")}

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
        score_df = score_df_raw[score_df_raw["ts_code"].isin(pool_set)].dropna(subset=FEATURE_COLS, how="all").copy()

        # ── 关键：股价过滤 ──
        if price_cap < 9999:
            score_df = score_df[score_df["close_qfq"].astype(float) <= price_cap]

        if len(score_df) < TOP_N:
            continue

        model = lgb.LGBMClassifier(
            boosting_type="gbdt", num_leaves=31, learning_rate=0.05,
            n_estimators=200, subsample=0.8, colsample_bytree=0.8,
            random_state=42, verbose=-1)
        model.fit(train_df[FEATURE_COLS], train_df["label"])
        scores = model.predict_proba(score_df[FEATURE_COLS])[:, 1]
        score_df = score_df.copy()
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
        for code in top20:
            try:
                entry_px = open_lookup.loc[(code, buy_date)]
                if pd.isna(entry_px) or entry_px <= 0:
                    continue
                stop_hit, stop_px, d = False, None, buy_date
                while d < sell_date:
                    d_next = next_trade_date.get(d)
                    if d_next is None: break
                    try: cl = close_lookup.loc[(code, d_next)]
                    except KeyError: d = d_next; continue
                    if not pd.isna(cl) and cl > 0 and cl / entry_px - 1 <= STOP_LOSS:
                        stop_hit, stop_px = True, cl; break
                    d = d_next
                if stop_hit:
                    r = float(stop_px) / float(entry_px) - 1
                else:
                    try: exit_px = open_lookup.loc[(code, sell_date)]
                    except KeyError: continue
                    if pd.isna(exit_px) or exit_px <= 0: continue
                    r = float(exit_px) / float(entry_px) - 1
                rets.append(r); valid_holdings.append(code)
            except KeyError:
                continue

        if len(rets) == 0:
            continue

        gross_ret = float(np.mean(rets))
        curr = set(valid_holdings)
        bought = curr - prev_holdings; sold = prev_holdings - curr
        nc = len(curr) if curr else 1; np_ = len(prev_holdings) if prev_holdings else 1
        bt = len(bought)/nc; st = len(sold)/np_ if np_ > 0 else 0
        cost = bt*BC + st*(SC+0.0005)
        net_ret = gross_ret - cost
        nav *= (1 + net_ret)
        trades.append({"rebalance_date": rd, "buy_date": buy_date, "sell_date": sell_date,
                        "period_return": round(net_ret, 6), "n_holdings": len(valid_holdings)})
        prev_holdings = curr

    return trades, nav

def main():
    t0 = time.time()
    log("加载面板和喜神池...")
    panel = pd.read_pickle(PANEL_PATH)
    pool = pd.read_csv(XISHEN_PATH); pool_set = set(pool["ts_code"])
    blacklist = load_blacklist()
    panel = panel[~panel["ts_code"].isin(blacklist)].reset_index(drop=True)
    panel = panel[~panel["ts_code"].str.endswith(".BJ")].reset_index(drop=True)
    panel = panel.dropna(subset=FEATURE_COLS, how="all")

    # 重算20天label
    log("重算label(20天)...")
    panel = panel.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    panel["fwd_ret_new"] = panel.groupby("ts_code")["close_qfq"].transform(lambda s: s.shift(-20)/s - 1)
    median_by_date = panel.groupby("trade_date")["fwd_ret_new"].transform("median")
    panel["label"] = (panel["fwd_ret_new"] > median_by_date).astype("Int8")
    panel.loc[panel["fwd_ret_new"].isna(), "label"] = pd.NA

    all_trade_dates = sorted(panel["trade_date"].unique())
    month_dates = get_month_start_dates(all_trade_dates)
    rebalance_dates = [d for d in month_dates if d >= BACKTEST_START]

    open_lookup = panel.set_index(["ts_code", "trade_date"])["open_qfq"].sort_index()
    close_lookup = panel.set_index(["ts_code", "trade_date"])["close_qfq"].sort_index()
    next_trade_date = {d: all_trade_dates[i+1] for i, d in enumerate(all_trade_dates) if i+1 < len(all_trade_dates)}

    log(f"面板就绪，喜神池{len(pool_set)}只，调仓日{len(rebalance_dates)}个")
    log("开始对比不同价格上限...")

    results = {}
    for cap in PRICE_CAPS:
        cap_label = f"≤{cap}元" if cap < 9999 else "无上限(原版)"
        log(f"  测试价格上限: {cap_label}...")
        trades, nav = run_backtest(panel, pool_set, rebalance_dates,
                                   open_lookup, close_lookup, next_trade_date, cap)
        if len(trades) == 0:
            log(f"  {cap_label}: 无有效交易"); continue
        pr = pd.Series([t["period_return"] for t in trades])
        nav_arr = (1+pr).cumprod()
        total = float(nav_arr.iloc[-1]-1)
        fd = pd.to_datetime(trades[0]["buy_date"]); ld = pd.to_datetime(trades[-1]["sell_date"])
        ny = (ld-fd).days/365.25
        ann = nav_arr.iloc[-1]**(1/ny)-1 if ny>0 else 0
        dd = float(((nav_arr-nav_arr.cummax())/nav_arr.cummax()).min())
        wr = float((pr>0).mean())
        sh = float(pr.mean()/pr.std()*np.sqrt(12)) if pr.std()>0 else 0
        results[cap_label] = {"ann":round(ann*100,2),"dd":round(dd*100,2),"sh":round(sh,3),
                               "wr":round(wr*100,1),"total":round(total*100,0),"n":len(trades)}
        log(f"  {cap_label}: 年化{ann*100:.1f}% 回撤{dd*100:.1f}% 夏普{sh:.2f} 胜率{wr*100:.1f}%")

    print("\n" + "="*70)
    print("  股价上限过滤 — 收益影响对比（S017基础配置: 20天月度喜神池）")
    print("="*70)
    print(f"  {'配置':<16}{'年化':>8}{'回撤':>8}{'夏普':>7}{'胜率':>7}{'总收益':>10}")
    for k,v in results.items():
        marker = " ◀ 原版" if "原版" in k else ""
        print(f"  {k:<16}{v['ann']:>7.1f}%{v['dd']:>7.1f}%{v['sh']:>7.2f}{v['wr']:>6.1f}%{v['total']:>9.0f}%{marker}")

    json.dump(results, open(f"{OUT_DIR}/price_cap_result.json","w"), ensure_ascii=False, indent=2)
    log(f"完成，耗时{time.time()-t0:.0f}s")

if __name__ == "__main__":
    main()
