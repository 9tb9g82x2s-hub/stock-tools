#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S009 随机基准对照实验

老大质疑：92.4%的高换手率是否意味着模型选股其实接近随机、收益是蒙出来的？
验证方法：在完全相同的股票池、完全相同的113个调仓时点、完全相同的T+1开盘价执行机制下，
每次调仓改为"随机选20只"而不是"模型打分选Top20"，重复200次模拟，
得到一个"纯随机策略"的年化收益分布。
如果模型的真实年化收益(42.5%毛收益)明显落在这个随机分布的右侧尾部之外，
说明模型选股确实有超越随机的能力；如果落在分布中间，说明和瞎选没有区别。
"""
import json
import sqlite3
import time
import numpy as np
import pandas as pd

BASE_DIR = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股"
PANEL_PATH = f"{BASE_DIR}/features_panel.pkl"
DB_PATH = "/Users/ziruzhu/stock-data/stock_all.db"

BACKTEST_START = "20170101"
TOP_N = 20
N_SIMULATIONS = 200
SEED_BASE = 1000


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


def main():
    t0 = time.time()
    log("读取特征面板...")
    panel = pd.read_pickle(PANEL_PATH)

    blacklist = load_blacklist()
    panel = panel[~panel["ts_code"].isin(blacklist)].reset_index(drop=True)
    panel = panel[~panel["ts_code"].str.endswith(".BJ")].reset_index(drop=True)

    all_trade_dates = sorted(panel["trade_date"].unique())
    month_dates = get_month_start_dates(all_trade_dates)
    rebalance_dates = [d for d in month_dates if d >= BACKTEST_START]
    log(f"调仓日数量: {len(rebalance_dates)}")

    panel_by_date = {d: sub for d, sub in panel.groupby("trade_date")}
    open_lookup = panel.set_index(["ts_code", "trade_date"])["open_qfq"].sort_index()
    next_trade_date = {d: all_trade_dates[i + 1] for i, d in enumerate(all_trade_dates) if i + 1 < len(all_trade_dates)}

    # 预先算好每个调仓日的候选股票池(与打分时score_df同样的过滤条件：特征不全空即可)
    eligible_pool = {}
    for rd in rebalance_dates:
        sub = panel_by_date.get(rd)
        if sub is None:
            eligible_pool[rd] = []
            continue
        FEATURE_LIKE_COLS = [c for c in sub.columns if c not in ("ts_code", "trade_date", "close_qfq", "open_qfq", "fwd_ret", "label")]
        sub2 = sub.dropna(subset=FEATURE_LIKE_COLS, how="all")
        eligible_pool[rd] = sub2["ts_code"].tolist()

    log("开始随机基准模拟...")
    annual_returns = []
    total_returns = []

    for sim in range(N_SIMULATIONS):
        rng = np.random.default_rng(SEED_BASE + sim)
        nav = 1.0
        period_rets = []

        for i, rd in enumerate(rebalance_dates):
            next_rd = rebalance_dates[i + 1] if i + 1 < len(rebalance_dates) else None
            if next_rd is None:
                break
            buy_date = next_trade_date.get(rd)
            sell_date = next_trade_date.get(next_rd)
            if buy_date is None or sell_date is None:
                continue

            pool = eligible_pool.get(rd, [])
            if len(pool) < TOP_N:
                continue
            picked = rng.choice(pool, size=TOP_N, replace=False)

            rets = []
            for code in picked:
                try:
                    p0 = open_lookup.loc[(code, buy_date)]
                    p1 = open_lookup.loc[(code, sell_date)]
                    if pd.isna(p0) or pd.isna(p1) or p0 <= 0:
                        continue
                    rets.append(float(p1) / float(p0) - 1)
                except KeyError:
                    continue

            if len(rets) == 0:
                continue

            period_ret = float(np.mean(rets))
            nav *= (1 + period_ret)
            period_rets.append(period_ret)

        n_periods = len(period_rets)
        n_years = n_periods / 12.0 if n_periods > 0 else 1
        annual_ret = (nav ** (1 / n_years) - 1) if n_years > 0 and nav > 0 else 0.0
        annual_returns.append(annual_ret)
        total_returns.append(nav - 1.0)

        if (sim + 1) % 50 == 0:
            log(f"已完成 {sim+1}/{N_SIMULATIONS} 次模拟")

    annual_returns = np.array(annual_returns)

    log(f"\n随机基准年化收益分布 (n={N_SIMULATIONS}次模拟):")
    log(f"  均值: {annual_returns.mean()*100:.2f}%")
    log(f"  标准差: {annual_returns.std()*100:.2f}%")
    log(f"  最小值: {annual_returns.min()*100:.2f}%")
    log(f"  25分位: {np.percentile(annual_returns, 25)*100:.2f}%")
    log(f"  中位数: {np.percentile(annual_returns, 50)*100:.2f}%")
    log(f"  75分位: {np.percentile(annual_returns, 75)*100:.2f}%")
    log(f"  95分位: {np.percentile(annual_returns, 95)*100:.2f}%")
    log(f"  99分位: {np.percentile(annual_returns, 99)*100:.2f}%")
    log(f"  最大值: {annual_returns.max()*100:.2f}%")

    # 模型实际毛年化收益(v1.2版本，不含手续费，与随机基准可比)
    model_gross_annual = 0.425
    percentile_rank = float((annual_returns < model_gross_annual).mean() * 100)
    log(f"\n模型实际毛年化收益 42.5% 在随机分布中的百分位: {percentile_rank:.1f}%")
    z_score = (model_gross_annual - annual_returns.mean()) / annual_returns.std()
    log(f"Z分数: {z_score:.2f} (即模型收益超出随机均值 {z_score:.2f} 个标准差)")

    result = {
        "n_simulations": N_SIMULATIONS,
        "random_annual_return_mean": float(annual_returns.mean()),
        "random_annual_return_std": float(annual_returns.std()),
        "random_annual_return_min": float(annual_returns.min()),
        "random_annual_return_p25": float(np.percentile(annual_returns, 25)),
        "random_annual_return_median": float(np.percentile(annual_returns, 50)),
        "random_annual_return_p75": float(np.percentile(annual_returns, 75)),
        "random_annual_return_p95": float(np.percentile(annual_returns, 95)),
        "random_annual_return_p99": float(np.percentile(annual_returns, 99)),
        "random_annual_return_max": float(annual_returns.max()),
        "model_gross_annual_return": model_gross_annual,
        "model_percentile_in_random_dist": percentile_rank,
        "model_z_score": float(z_score),
        "all_random_annual_returns": annual_returns.tolist(),
    }
    with open(f"{BASE_DIR}/random_baseline_result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log(f"\n结果已写出: {BASE_DIR}/random_baseline_result.json")
    log(f"总耗时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
