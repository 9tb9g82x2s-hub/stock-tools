#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S013 喜神池 · 止损位优化（敏感性扫描）

复用 S013 已选出的114期持仓(trades_s013.csv)，只改止损阈值重算收益，
不重训模型。扫描 6%/8%/10%/12%/15%/20% + 无止损，找最优止损位。

核心筛选指标 Calmar = 年化/|最大回撤|（单位回撤的收益效率）。
口径与 S009 的 stoploss_sensitivity.py 完全一致，保证可横向比较。
"""
import json, sqlite3, time, ast
import numpy as np, pandas as pd

BASE = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S013-喜神池LightGBM选股"
DB = "/Users/ziruzhu/stock-data/stock_all.db"
STOP_LEVELS = [0.06, 0.08, 0.10, 0.12, 0.15, 0.20]
BUY_C, SELL_C, STAMP = 0.00025, 0.00025, 0.0005


def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


log("读S013持仓+预加载日线...")
t = pd.read_csv(f"{BASE}/trades_s013.csv")
all_codes = set()
for _, r in t.iterrows():
    all_codes.update(ast.literal_eval(r["holdings"]))
mn, mx = str(t["buy_date"].min()), str(t["sell_date"].max())
con = sqlite3.connect(DB)
cs = ",".join([f"'{c}'" for c in all_codes])
px = pd.read_sql(f"SELECT ts_code,trade_date,open_qfq,close_qfq FROM stk_factor "
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


def sim(code, bd, sd, stop_pct):
    try: p0 = open_lk.loc[(code, bd)]
    except KeyError: return None, False
    if pd.isna(p0) or p0 <= 0: return None, False
    if stop_pct is not None and code in close_by:
        sl = p0 * (1 - stop_pct)
        dates, closes = close_by[code]
        mask = (dates > bd) & (dates <= sd)
        for d, c in zip(dates[mask], closes[mask]):
            if pd.notna(c) and c <= sl:
                return float(c) / float(p0) - 1, True
    try:
        p1 = open_lk.loc[(code, sd)]
        if pd.isna(p1) or p1 <= 0: return None, False
        return float(p1) / float(p0) - 1, False
    except KeyError: return None, False


def backtest(stop_pct):
    nav = 1.0; prs = []; navc = []; prev = set(); n_stop = 0; year_r = {}
    for _, r in t.iterrows():
        codes = ast.literal_eval(r["holdings"]); bd = str(r["buy_date"]); sd = str(r["sell_date"])
        rets = []; valid = []; ns = 0
        for c in codes:
            ret, stp = sim(c, bd, sd, stop_pct)
            if ret is None: continue
            rets.append(ret); valid.append(c)
            if stp: ns += 1
        if not rets: continue
        n_stop += ns
        gross = float(np.mean(rets)); curr = set(valid)
        bt = len(curr - prev) / (len(curr) or 1)
        st = len(prev - curr) / (len(prev) or 1) if prev else 0
        extra = (ns / (len(curr) or 1)) * (SELL_C + STAMP)
        cost = bt * BUY_C + st * (SELL_C + STAMP) + extra
        pr = gross - cost; nav *= (1 + pr); prs.append(pr)
        navc.append(nav)
        yr = str(r["rebalance_date"])[:4]
        year_r.setdefault(yr, []).append(pr)
        prev = curr
    pr = np.array(prs); n = len(pr); ny = n / 12.0
    ann = (nav ** (1/ny) - 1) if ny > 0 and nav > 0 else 0
    navs = np.array([1.0] + navc); dd = float((navs / np.maximum.accumulate(navs) - 1).min())
    sharpe = float(pr.mean() / pr.std() * np.sqrt(12)) if n > 1 and pr.std() > 0 else 0
    win = float(np.mean(pr > 0))
    yearly = {yr: round((np.prod([1+x for x in v]) - 1) * 100, 1) for yr, v in year_r.items()}
    return {"total": round((nav-1)*100, 1), "annual": round(ann*100, 2), "mdd": round(dd*100, 2),
            "sharpe": round(sharpe, 2), "win": round(win*100, 1), "n_stop": n_stop, "yearly": yearly}


results = {}
log("无止损基线...")
results["none"] = backtest(None)
for s in STOP_LEVELS:
    log(f"止损{int(s*100)}%...")
    results[f"{int(s*100)}%"] = backtest(s)

json.dump(results, open(f"{BASE}/stoploss_opt_s013_result.json", "w"), ensure_ascii=False, indent=2)


def calmar(r): return round(r["annual"] / abs(r["mdd"]), 3) if r["mdd"] != 0 else 0


print("\n" + "="*82)
print("S013 喜神池 · 止损位敏感性扫描（复用114期持仓，唯一变量=止损阈值）")
print("="*82)
print(f"{'档位':<10}{'累计收益':>12}{'年化':>9}{'最大回撤':>11}{'夏普':>8}{'Calmar':>9}{'止损笔数':>9}")
print("-"*82)
order = ["none", "6%", "8%", "10%", "12%", "15%", "20%"]
names = {"none":"无止损","6%":"止损6%","8%":"止损8%","10%":"止损10%",
         "12%":"止损12%(现)","15%":"止损15%","20%":"止损20%"}
best_calmar = max(order, key=lambda k: calmar(results[k]))
best_sharpe = max(order, key=lambda k: results[k]["sharpe"])
best_ann = max(order, key=lambda k: results[k]["annual"])
for k in order:
    r = results[k]
    mark = ""
    if k == best_calmar: mark += " ★Calmar最优"
    if k == best_sharpe: mark += " ◆夏普最优"
    if k == best_ann: mark += " ▲年化最优"
    print(f"{names[k]:<9}{r['total']:>11.0f}%{r['annual']:>8.1f}%{r['mdd']:>10.1f}%"
          f"{r['sharpe']:>8.2f}{calmar(r):>9.3f}{r['n_stop']:>9}{mark}")
print("="*82)

print("\n逐年收益对比(%):")
yrs = sorted(results["none"]["yearly"].keys())
print(f"{'年份':<7}" + "".join(f"{names[k]:>12}" for k in order))
for yr in yrs:
    print(f"{yr:<7}" + "".join(f"{results[k]['yearly'].get(yr,0):>+11.1f}%" for k in order))

print(f"\n【结论】")
print(f"  当前S013用12%止损。Calmar最优档: {names[best_calmar]}(={calmar(results[best_calmar])})")
print(f"  夏普最优档: {names[best_sharpe]}({results[best_sharpe]['sharpe']})")
print(f"  年化最优档: {names[best_ann]}({results[best_ann]['annual']}%)")
print(f"  注: Calmar=年化/|回撤|, 综合收益与抗跌的最佳平衡指标")
log("结果已存 stoploss_opt_s013_result.json")
