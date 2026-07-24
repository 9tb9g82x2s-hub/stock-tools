#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S009 v1.5 C1止损(12%收盘价) × 去掉每期最强K只赢家

两个验证叠加：
1. C1口径止损(12%收盘价止损，strategy.md归档版本)
2. 每期持仓中去掉最终收益最高的K只（K=0/1/2/3）

核心问题：在C1止损的基础上，剔除每期头部赢家，收益率是否还能跑赢沪深300(+44.6%累计)?

注意：止损先发生 → 才算到期最终收益。
即"去掉最强K只"是在用含止损逻辑算完每只票的实际收益之后，再按实际收益排序去掉最高的K只。
这才是真实场景：止损条件下的最终实际收益排名，而不是按原始无止损收益排名。
"""
import json, sqlite3, time, ast
import numpy as np, pandas as pd

BASE = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股"
DB = "/Users/ziruzhu/stock-data/stock_all.db"
STOP_PCT = 0.12       # v1.5正式止损线：12%
DROP_K_LIST = [0, 1, 2, 3]
BUY_C, SELL_C, STAMP = 0.00025, 0.00025, 0.0005

HS300 = {"2017":21.78,"2018":-25.31,"2019":36.07,"2020":27.21,"2021":-5.2,
         "2022":-21.63,"2023":-11.38,"2024":14.68,"2025":17.66,"2026":3.39}

def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

log("读 trades_full.csv...")
t = pd.read_csv(f"{BASE}/trades_full.csv")
all_codes = set()
for _, r in t.iterrows():
    all_codes.update(ast.literal_eval(r["holdings"]))
mn, mx = str(t["buy_date"].min()), str(t["sell_date"].max())

log("预加载日线(open_qfq + close_qfq)...")
con = sqlite3.connect(DB)
cs = ",".join([f"'{c}'" for c in all_codes])
px = pd.read_sql(
    f"SELECT ts_code,trade_date,open_qfq,close_qfq FROM stk_factor "
    f"WHERE trade_date BETWEEN '{mn}' AND '{mx}' AND ts_code IN ({cs})", con)
con.close()
px["open_qfq"] = pd.to_numeric(px["open_qfq"], errors="coerce")
px["close_qfq"] = pd.to_numeric(px["close_qfq"], errors="coerce")
px = px.sort_values(["ts_code", "trade_date"])
open_lk = px.set_index(["ts_code", "trade_date"])["open_qfq"]
close_by = {}
for code, sub in px.groupby("ts_code"):
    close_by[code] = (sub["trade_date"].values, sub["close_qfq"].values)
log(f"日线 {len(px):,} 行, {len(all_codes)} 股")


def sim_c1(code, bd, sd):
    """单笔收益，含C1口径12%收盘价止损。返回实际收益率(含止损)。"""
    try:
        p0 = open_lk.loc[(code, bd)]
    except KeyError:
        return None
    if pd.isna(p0) or p0 <= 0:
        return None
    sl = p0 * (1 - STOP_PCT)
    if code in close_by:
        dates, closes = close_by[code]
        mask = (dates > bd) & (dates <= sd)
        for d, c in zip(dates[mask], closes[mask]):
            if pd.notna(c) and c <= sl:
                return float(c) / float(p0) - 1  # 止损出场
    try:
        p1 = open_lk.loc[(code, sd)]
        if pd.isna(p1) or p1 <= 0:
            return None
        return float(p1) / float(p0) - 1
    except KeyError:
        return None


def backtest_drop_k(drop_k):
    """C1止损 + 去掉每期实际收益最高的drop_k只，剩余等权"""
    nav = 1.0; prs = []; navc = []; prev = set(); year_r = {}
    for _, r in t.iterrows():
        codes = ast.literal_eval(r["holdings"])
        bd, sd = str(r["buy_date"]), str(r["sell_date"])
        # 用C1止损算每只股票的实际收益
        stock_rets = []
        for code in codes:
            ret = sim_c1(code, bd, sd)
            if ret is not None:
                stock_rets.append((code, ret))
        if len(stock_rets) == 0:
            continue
        # 按实际收益降序，去掉最高的drop_k只（止损后的真实收益排名）
        stock_rets_sorted = sorted(stock_rets, key=lambda x: x[1], reverse=True)
        kept = stock_rets_sorted[drop_k:] if drop_k < len(stock_rets_sorted) else []
        if len(kept) == 0:
            continue
        rets_kept = [r for _, r in kept]
        valid = {code for code, _ in kept}
        gross = float(np.mean(rets_kept))
        # 交易成本（基于实际持仓变化，去掉的赢家相当于额外换手）
        bt = len(valid - prev) / (len(valid) or 1)
        st = len(prev - valid) / (len(prev) or 1) if prev else 0
        cost = bt * BUY_C + st * (SELL_C + STAMP)
        pr = gross - cost
        nav *= (1 + pr); prs.append(pr); navc.append(nav)
        yr = str(r["rebalance_date"])[:4]
        year_r.setdefault(yr, []).append(pr)
        prev = valid

    pr_arr = np.array(prs); n = len(pr_arr); ny = n / 12.0
    ann = (nav ** (1/ny) - 1) if ny > 0 and nav > 0 else 0
    navs = np.array([1.0] + navc)
    dd = float((navs / np.maximum.accumulate(navs) - 1).min())
    sharpe = float(pr_arr.mean() / pr_arr.std() * np.sqrt(12)) if n > 1 and pr_arr.std() > 0 else 0
    win = float(np.mean(pr_arr > 0))
    yearly = {yr: round((np.prod([1+x for x in v]) - 1) * 100, 1) for yr, v in year_r.items()}
    return {
        "total": round((nav-1)*100, 1),
        "annual": round(ann*100, 2),
        "mdd": round(dd*100, 2),
        "sharpe": round(sharpe, 2),
        "win": round(win*100, 1),
        "n_periods": n,
        "yearly": yearly,
    }


log("开始计算...")
results = {}
for k in DROP_K_LIST:
    label = f"C1_drop{k}"
    log(f"C1止损12% + 去掉每期最强{k}只...")
    results[label] = backtest_drop_k(k)
    r = results[label]
    log(f"  drop{k}: 累计{r['total']:.0f}%  年化{r['annual']:.1f}%  回撤{r['mdd']:.1f}%  夏普{r['sharpe']:.2f}")

# 沪深300基准
hs_nav = 1.0
for y, rv in HS300.items():
    hs_nav *= (1 + rv / 100)
hs_total = (hs_nav - 1) * 100

out = {"c1_drop_winners": results, "hs300_cumulative_pct": round(hs_total, 1)}
with open(f"{BASE}/c1_drop_winners_result.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print("\n" + "=" * 76)
print(f"  沪深300基准累计: {hs_total:.1f}%")
print("=" * 76)
labels = {0: "C1止损12%(全20只)", 1: "C1止损+去最强1只", 2: "C1止损+去最强2只", 3: "C1止损+去最强3只"}
print(f"{'版本':<26}{'累计收益':>12}{'年化':>9}{'最大回撤':>11}{'夏普':>8}  vs沪深300")
print("-" * 76)
for k in DROP_K_LIST:
    key = f"C1_drop{k}"
    r = results[key]
    win = "跑赢✅" if r["total"] > hs_total else "跑输❌"
    print(f"{labels[k]:<24}{r['total']:>11.0f}%{r['annual']:>8.1f}%{r['mdd']:>10.1f}%{r['sharpe']:>8.2f}   {win}")
print("=" * 76)

print("\n各版本逐年收益(%) — C1止损12%基础上去掉每期最强K只:")
yrs = sorted(results["C1_drop0"]["yearly"].keys())
print(f"{'年份':<7}" + "".join(f"{'去'+str(k)+'只':>10}" for k in DROP_K_LIST) + f"{'沪深300':>10}")
for yr in yrs:
    line = f"{yr:<7}"
    for k in DROP_K_LIST:
        key = f"C1_drop{k}"
        line += f"{results[key]['yearly'].get(yr, 0):>+9.1f}%"
    line += f"{HS300.get(yr, 0):>+9.1f}%"
    print(line)

print("\n注：止损触发后以收盘价出场，去掉最强K只是按含止损的实际最终收益排名，不是原始无止损收益。")
