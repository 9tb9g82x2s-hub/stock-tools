#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S009 滑点敏感性测试

背景：v1.3已计入手续费+印花税(拖累年化仅1.53pp)，但92.4%的超高换手率意味着
每期几乎全仓换股，买卖时的滑点(挂单价与实际成交价的偏离)可能是比手续费更大的
隐藏成本，尤其是策略持仓偏中小盘股，流动性天然不如沪深300成分股。

方法：在已有trades_full.csv(含每期gross_return、buy_turnover、sell_turnover)基础上，
按不同滑点档位(万5/千1/千2/千3)分别在买入和卖出两端各扣一次滑点成本，
只对实际发生换手的部分收取(与手续费逻辑一致)，重新计算年化收益/回撤/夏普，
对比不同滑点假设下策略还剩多少空间。
"""
import json
import numpy as np
import pandas as pd
import ast

BASE_DIR = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股"

SLIPPAGE_SCENARIOS = {
    "无滑点(仅手续费,即v1.3)": 0.0,
    "万5(流动性较好)": 0.0005,
    "千1(中等流动性)": 0.001,
    "千2(流动性偏差/换手率高时常见)": 0.002,
    "千3(小盘股保守估计)": 0.003,
}


def compute_metrics(period_rets, dates):
    nav = 1.0
    nav_curve = [1.0]
    for r in period_rets:
        nav *= (1 + r)
        nav_curve.append(nav)
    n_periods = len(period_rets)
    n_years = n_periods / 12.0
    annual_return = (nav ** (1 / n_years) - 1) if n_years > 0 and nav > 0 else 0.0
    total_return = nav - 1.0

    nav_arr = np.array(nav_curve)
    running_max = np.maximum.accumulate(nav_arr)
    drawdown = nav_arr / running_max - 1
    max_drawdown = float(drawdown.min())

    win_rate = float(np.mean(np.array(period_rets) > 0))
    rets_arr = np.array(period_rets)
    sharpe = float(rets_arr.mean() / rets_arr.std() * np.sqrt(12)) if rets_arr.std() > 0 else 0.0

    return {
        "annual_return": annual_return,
        "total_return": total_return,
        "max_drawdown": max_drawdown,
        "win_rate": win_rate,
        "sharpe_ratio": sharpe,
        "final_nav": nav,
    }


def main():
    df = pd.read_csv(f"{BASE_DIR}/trades_full.csv")
    gross_rets = df["gross_return"].values
    buy_turnover = df["buy_turnover"].values
    sell_turnover = df["sell_turnover"].values
    existing_cost = df["trading_cost"].values  # 已有的手续费+印花税成本

    results = {}
    print(f"{'滑点情景':<28} {'年化收益':>10} {'总收益':>10} {'最大回撤':>10} {'夏普':>8} {'胜率':>8}")
    print("-" * 80)

    for name, slip_rate in SLIPPAGE_SCENARIOS.items():
        # 买入滑点：对buy_turnover部分收取；卖出滑点：对sell_turnover部分收取
        slip_cost = (buy_turnover + sell_turnover) * slip_rate
        # v1.3已有的手续费成本 + 新增滑点成本
        total_cost = existing_cost + slip_cost
        net_rets = gross_rets - total_cost

        m = compute_metrics(net_rets.tolist(), df["rebalance_date"].tolist())
        results[name] = m
        print(f"{name:<28} {m['annual_return']*100:>9.2f}% {m['total_return']*100:>9.0f}% {m['max_drawdown']*100:>9.2f}% {m['sharpe_ratio']:>8.2f} {m['win_rate']*100:>7.1f}%")

    avg_turnover = float(((buy_turnover + sell_turnover) / 2).mean())
    print(f"\n平均单期换手率: {avg_turnover*100:.1f}%")
    print(f"平均单期总换手量(买+卖): {(buy_turnover + sell_turnover).mean()*100:.1f}%")

    out = {
        "avg_turnover_per_period": avg_turnover,
        "scenarios": {k: {kk: float(vv) for kk, vv in v.items()} for k, v in results.items()},
    }
    with open(f"{BASE_DIR}/slippage_sensitivity_result.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n结果已写出: {BASE_DIR}/slippage_sensitivity_result.json")


if __name__ == "__main__":
    main()
