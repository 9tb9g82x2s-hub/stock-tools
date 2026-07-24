#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S009 持仓集中度测试 —— 探索"提高年化到100%"的可能性

背景：v1.3(Top20等权)年化40.97%(含手续费)。老大想知道有没有办法把收益拉到100%。
最直接、不需要额外交易权限(如融资融券)的一条路：提高持仓集中度。
只选模型打分最高的5/10只而不是20只，如果模型排序能力强，头部股票的超额收益会更集中，
但代价是集中度越高，单只黑天鹅事件对组合的冲击也越大，回撤会明显放大。

方法：与v1.3完全相同的训练流程(同一份特征面板、同一批调仓日、同一套LightGBM参数)，
但每期只训练一次模型，对同一个打分结果分别截取Top5/Top10/Top15/Top20做对比，
避免重复训练造成的时间浪费。同样计入T+1开盘价执行+手续费印花税成本。

结果不会覆盖v1.3的results.json/train_backtest.py，独立产出对比报告。
"""
import json
import sqlite3
import time
import numpy as np
import pandas as pd
import lightgbm as lgb

BASE_DIR = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股"
PANEL_PATH = f"{BASE_DIR}/features_panel.pkl"
DB_PATH = "/Users/ziruzhu/stock-data/stock_all.db"

BACKTEST_START = "20170101"
TRAIN_MONTHS = 12
TOP_N_CANDIDATES = [3, 5, 6, 7, 8, 10]  # 对比不同集中度，聚焦Top5附近寻找更优点

BUY_COMMISSION = 0.00025
SELL_COMMISSION = 0.00025
STAMP_TAX = 0.0005

FEATURE_COLS = [
    "mom_5", "mom_10", "mom_20", "mom_60", "mom_120",
    "turnover_rate", "turnover_rate_f", "volume_ratio", "vol_chg_20",
    "bias_5", "bias_10", "bias_20", "bias_60",
    "macd_dif", "macd_dea", "macd", "kdj_k", "kdj_d", "kdj_j",
    "rsi_6", "rsi_12", "rsi_24", "cci", "boll_pct", "boll_width",
    "pe", "pe_ttm", "pb", "ps", "ps_ttm", "dv_ttm",
    "net_mf_ratio", "lg_buy_ratio",
]


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


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
    firsts = df.groupby("ym").first()
    return firsts["trade_date"].tolist()


def train_and_score(train_df, score_df):
    X_train = train_df[FEATURE_COLS]
    y_train = train_df["label"]
    model = lgb.LGBMClassifier(
        boosting_type="gbdt", num_leaves=31, learning_rate=0.05,
        n_estimators=200, subsample=0.8, colsample_bytree=0.8,
        random_state=42, verbose=-1,
    )
    model.fit(X_train, y_train)
    scores = model.predict_proba(score_df[FEATURE_COLS])[:, 1]
    return scores


def compute_metrics(period_rets, gross_rets):
    nav = 1.0
    nav_curve = [1.0]
    for r in period_rets:
        nav *= (1 + r)
        nav_curve.append(nav)
    n_periods = len(period_rets)
    n_years = n_periods / 12.0 if n_periods > 0 else 1
    annual_return = (nav ** (1 / n_years) - 1) if n_years > 0 and nav > 0 else 0.0
    total_return = nav - 1.0

    nav_arr = np.array(nav_curve)
    running_max = np.maximum.accumulate(nav_arr)
    drawdown = nav_arr / running_max - 1
    max_drawdown = float(drawdown.min())

    rets_arr = np.array(period_rets)
    win_rate = float(np.mean(rets_arr > 0)) if n_periods > 0 else 0.0
    sharpe = float(rets_arr.mean() / rets_arr.std() * np.sqrt(12)) if n_periods > 1 and rets_arr.std() > 0 else 0.0

    gross_nav = float(np.prod(1 + np.array(gross_rets))) if len(gross_rets) > 0 else 1.0
    gross_annual = (gross_nav ** (1 / n_years) - 1) if n_years > 0 and gross_nav > 0 else 0.0

    return {
        "annual_return": annual_return, "gross_annual_return": gross_annual,
        "total_return": total_return, "max_drawdown": max_drawdown,
        "win_rate": win_rate, "sharpe_ratio": sharpe, "n_periods": n_periods,
    }, nav_curve


def main():
    t0 = time.time()
    log("读取特征面板...")
    panel = pd.read_pickle(PANEL_PATH)

    blacklist = load_blacklist()
    panel = panel[~panel["ts_code"].isin(blacklist)].reset_index(drop=True)
    panel = panel[~panel["ts_code"].str.endswith(".BJ")].reset_index(drop=True)
    panel = panel.dropna(subset=FEATURE_COLS, how="all")

    all_trade_dates = sorted(panel["trade_date"].unique())
    month_dates = get_month_start_dates(all_trade_dates)
    rebalance_dates = [d for d in month_dates if d >= BACKTEST_START]
    log(f"调仓日数量: {len(rebalance_dates)}")

    panel_by_date = {d: sub for d, sub in panel.groupby("trade_date")}
    open_lookup = panel.set_index(["ts_code", "trade_date"])["open_qfq"].sort_index()
    next_trade_date = {d: all_trade_dates[i + 1] for i, d in enumerate(all_trade_dates) if i + 1 < len(all_trade_dates)}

    # 每个TOP_N候选各自维护净值曲线、持仓历史(用于换手/成本计算)
    state = {n: {"period_rets": [], "gross_rets": [], "prev_holdings": set(), "nav_curve": []} for n in TOP_N_CANDIDATES}

    max_top_n = max(TOP_N_CANDIDATES)

    for i, rd in enumerate(rebalance_dates):
        rd_dt = pd.to_datetime(rd, format="%Y%m%d")
        train_start = (rd_dt - pd.DateOffset(months=TRAIN_MONTHS)).strftime("%Y%m%d")
        train_mask = (panel["trade_date"] >= train_start) & (panel["trade_date"] < rd)
        train_df = panel.loc[train_mask].dropna(subset=FEATURE_COLS + ["label"])

        if len(train_df) < 5000:
            continue

        score_df = panel_by_date.get(rd)
        if score_df is None or len(score_df) == 0:
            continue
        score_df = score_df.dropna(subset=FEATURE_COLS, how="all").copy()

        # 关键优化：只训练一次模型，供所有TOP_N候选共用
        scores = train_and_score(train_df, score_df)
        score_df["score"] = scores
        ranked = score_df.sort_values("score", ascending=False)
        top_max = ranked.head(max_top_n)["ts_code"].tolist()

        next_rd = rebalance_dates[i + 1] if i + 1 < len(rebalance_dates) else None
        if next_rd is None:
            break
        buy_date = next_trade_date.get(rd)
        sell_date = next_trade_date.get(next_rd)
        if buy_date is None or sell_date is None:
            continue

        for n in TOP_N_CANDIDATES:
            top_n_codes = top_max[:n]
            rets = []
            valid_holdings = []
            for code in top_n_codes:
                try:
                    p0 = open_lookup.loc[(code, buy_date)]
                    p1 = open_lookup.loc[(code, sell_date)]
                    if pd.isna(p0) or pd.isna(p1) or p0 <= 0:
                        continue
                    rets.append(float(p1) / float(p0) - 1)
                    valid_holdings.append(code)
                except KeyError:
                    continue

            if len(rets) == 0:
                continue

            gross_ret = float(np.mean(rets))
            curr_holdings = set(valid_holdings)
            s = state[n]
            prev_holdings = s["prev_holdings"]
            bought = curr_holdings - prev_holdings
            sold = prev_holdings - curr_holdings
            n_curr = len(curr_holdings) if curr_holdings else 1
            n_prev = len(prev_holdings) if prev_holdings else 1
            buy_turnover = len(bought) / n_curr
            sell_turnover = len(sold) / n_prev if n_prev > 0 else 0.0
            cost = buy_turnover * BUY_COMMISSION + sell_turnover * (SELL_COMMISSION + STAMP_TAX)
            net_ret = gross_ret - cost

            s["period_rets"].append(net_ret)
            s["gross_rets"].append(gross_ret)
            s["prev_holdings"] = curr_holdings
            prev_nav = s["nav_curve"][-1] if s["nav_curve"] else 1.0
            s["nav_curve"].append(prev_nav * (1 + net_ret))

        if (i + 1) % 12 == 0:
            log(f"已完成 {i+1}/{len(rebalance_dates)} 期调仓")

    log("计算各集中度指标...")
    results = {}
    print(f"\n{'持仓数':<10} {'年化收益':>10} {'总收益':>10} {'最大回撤':>10} {'夏普':>8} {'胜率':>8}")
    print("-" * 65)
    for n in TOP_N_CANDIDATES:
        s = state[n]
        m, nav_curve = compute_metrics(s["period_rets"], s["gross_rets"])
        results[f"top_{n}"] = m
        print(f"Top{n:<8} {m['annual_return']*100:>9.2f}% {m['total_return']*100:>9.0f}% {m['max_drawdown']*100:>9.2f}% {m['sharpe_ratio']:>8.2f} {m['win_rate']*100:>7.1f}%")

    with open(f"{BASE_DIR}/concentration_test_result_fine.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    log(f"\n结果已写出: {BASE_DIR}/concentration_test_result.json")
    log(f"总耗时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
