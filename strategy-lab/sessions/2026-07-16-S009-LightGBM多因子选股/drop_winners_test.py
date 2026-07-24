#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
去掉机会票稳健性测试：
每期Top20持仓中，去掉涨幅最高的 K 只（K=0/1/2/3），用剩余等权重算每期收益，
累计净值，看剔除尾部大赢家后是否还能跑赢沪深300。

口径与原回测一致：用 features_panel 的 open_qfq（前复权），T+1开盘买→下期T+1开盘卖。
持仓直接读 trades_full.csv（原版静态名单选出，幸存者偏差已验证影响<7%）。
成本：按原版口径，每期扣一个固定的换手成本估计（buy_turnover/sell_turnover已在csv里）。
"""
import json, ast, time
import numpy as np
import pandas as pd

BASE = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股"
PANEL = f"{BASE}/features_panel.pkl"

BUY_COMMISSION = 0.00025
SELL_COMMISSION = 0.00025
STAMP_TAX = 0.0005

def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

log("读 trades_full.csv...")
t = pd.read_csv(f"{BASE}/trades_full.csv")
t["holdings_list"] = t["holdings"].apply(ast.literal_eval)

log("读 panel (open_qfq)...")
panel = pd.read_pickle(PANEL)
# 建 (ts_code, trade_date) -> open_qfq 查表
op = panel.set_index(["ts_code", "trade_date"])["open_qfq"].sort_index()

def period_stock_returns(row):
    """返回该期每只票的收益list（buy_date开盘→sell_date开盘）"""
    bd = str(row["buy_date"]); sd = str(row["sell_date"])
    rets = []
    for code in row["holdings_list"]:
        try:
            p0 = op.loc[(code, bd)]; p1 = op.loc[(code, sd)]
            if pd.isna(p0) or pd.isna(p1) or p0 <= 0:
                continue
            rets.append(float(p1)/float(p0) - 1)
        except KeyError:
            continue
    return rets

# 预先算每期成本（和原版一致）
def period_cost(row):
    bc = row["buy_turnover"] * BUY_COMMISSION
    sc = row["sell_turnover"] * (SELL_COMMISSION + STAMP_TAX)
    return bc + sc

log("逐期计算...")
results = {}
for K in [0, 1, 2, 3]:
    nav = 1.0
    navs = []
    yearly = {}
    for _, row in t.iterrows():
        rets = period_stock_returns(row)
        if len(rets) == 0:
            continue
        rets_sorted = sorted(rets, reverse=True)  # 降序
        kept = rets_sorted[K:] if K < len(rets_sorted) else []  # 去掉最高的K只
        if len(kept) == 0:
            continue
        gross = float(np.mean(kept))
        net = gross - period_cost(row)
        nav *= (1 + net)
        yr = str(row["rebalance_date"])[:4]
        yearly.setdefault(yr, 1.0)
        yearly[yr] *= (1 + net)
        navs.append(nav)
    total = nav - 1.0
    n_years = len(t) / 12.0
    annual = nav ** (1/n_years) - 1
    # 最大回撤
    arr = np.array([1.0] + navs)
    rm = np.maximum.accumulate(arr)
    mdd = float((arr/rm - 1).min())
    yearly_pct = {y: round((v-1)*100, 2) for y, v in yearly.items()}
    results[K] = {
        "total_return_pct": round(total*100, 1),
        "annual_return_pct": round(annual*100, 2),
        "max_drawdown_pct": round(mdd*100, 2),
        "final_nav": round(nav, 3),
        "yearly": yearly_pct,
    }
    log(f"K={K} (去掉每期涨幅最高{K}只): 累计{total*100:.0f}%  年化{annual*100:.1f}%  回撤{mdd*100:.1f}%")

# 沪深300基准
HS300 = {"2017":21.78,"2018":-25.31,"2019":36.07,"2020":27.21,"2021":-5.2,
         "2022":-21.63,"2023":-11.38,"2024":14.68,"2025":17.66,"2026":3.39}
hs_nav = 1.0
for y, r in HS300.items():
    hs_nav *= (1 + r/100)
hs_total = (hs_nav - 1) * 100

json.dump({"drop_winners": results, "hs300_cumulative_pct": round(hs_total,1)},
          open(f"{BASE}/drop_winners_result.json","w"), ensure_ascii=False, indent=2)

print("\n" + "="*66)
print(f"{'版本':<24}{'累计收益':>12}{'年化':>10}{'最大回撤':>10}  vs沪深300")
print("-"*66)
labels = {0:"原版(全部20只)", 1:"去掉每期最强1只", 2:"去掉每期最强2只", 3:"去掉每期最强3只"}
for K in [0,1,2,3]:
    r = results[K]
    win = "跑赢✅" if r["total_return_pct"] > hs_total else "跑输❌"
    print(f"{labels[K]:<22}{r['total_return_pct']:>10.0f}%{r['annual_return_pct']:>9.1f}%{r['max_drawdown_pct']:>9.1f}%   {win}")
print("-"*66)
print(f"{'沪深300基准':<22}{hs_total:>10.0f}%")
print("="*66)

print("\n各版本逐年收益(%):")
yrs = sorted(results[0]["yearly"].keys())
print(f"{'年份':<8}" + "".join(f"{'去'+str(K)+'只':>10}" for K in [0,1,2,3]) + f"{'沪深300':>10}")
for y in yrs:
    line = f"{y:<8}"
    for K in [0,1,2,3]:
        line += f"{results[K]['yearly'].get(y,0):>9.1f}%"
    line += f"{HS300.get(y,0):>9.1f}%"
    print(line)
