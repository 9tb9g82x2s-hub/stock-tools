#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按年汇总 S009 十年回测：
- 策略组合年度收益（复利）
- 全市场中位数年度收益（同期，作为大盘代理）
每期用 buy_date→sell_date 计算全市场中位数收益，再按年复利汇总。
"""
import sqlite3, json, time
import pandas as pd, numpy as np

DB = '/Users/ziruzhu/stock-data/stock_all.db'
BASE = '/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股'

def log(m): print("[%s] %s" % (time.strftime('%H:%M:%S'), m), flush=True)

con = sqlite3.connect(DB)
trades = pd.read_csv(BASE + '/trades_full.csv')
log("回测期数: %d, 首期 %s, 末期 %s" % (len(trades), trades['rebalance_date'].iloc[0], trades['rebalance_date'].iloc[-1]))

# 预加载所有需要的日期的全市场open/close
buy_dates = trades['buy_date'].astype(str).tolist()
sell_dates = trades['sell_date'].astype(str).tolist()
all_needed = sorted(set(buy_dates) | set(sell_dates))
log("需要 %d 个交易日的全市场数据" % len(all_needed))

dstr = ",".join(["'%s'" % d for d in all_needed])
mkt = pd.read_sql("SELECT ts_code,trade_date,open,close FROM daily WHERE trade_date IN (%s)" % dstr, con)
mkt["open"] = pd.to_numeric(mkt["open"], errors="coerce")
mkt["close"] = pd.to_numeric(mkt["close"], errors="coerce")
con.close()

open_pivot = mkt.pivot_table(index="ts_code", columns="trade_date", values="open")
close_pivot = mkt.pivot_table(index="ts_code", columns="trade_date", values="close")

# 每期计算全市场中位数收益（buy开盘 -> sell收盘）
period_rows = []
for _, t in trades.iterrows():
    bd = str(t["buy_date"]); sd = str(t["sell_date"])
    if bd not in open_pivot.columns or sd not in close_pivot.columns:
        mkt_med = np.nan
    else:
        o = open_pivot[bd]; c = close_pivot[sd]
        m = pd.DataFrame({"o": o, "c": c}).dropna()
        m = m[m["o"] > 0]
        m["r"] = m["c"] / m["o"] - 1
        mkt_med = float(m["r"].median())
    period_rows.append({
        "rebalance_date": str(t["rebalance_date"]),
        "sell_date": sd,
        "year": str(t["rebalance_date"])[:4],
        "strat_ret": float(t["period_return"]),
        "mkt_median_ret": mkt_med,
        "win_count": int(t["win_count"]),
    })

pdf = pd.DataFrame(period_rows)

# 按年复利汇总
def compound(x): return float(np.prod(1 + x) - 1)
yearly = []
for yr, g in pdf.groupby("year"):
    strat_y = compound(g["strat_ret"].values)
    mkt_y = compound(g["mkt_median_ret"].dropna().values) if g["mkt_median_ret"].notna().any() else np.nan
    yearly.append({
        "year": yr,
        "periods": len(g),
        "strat_return_pct": round(strat_y * 100, 2),
        "mkt_median_return_pct": round(mkt_y * 100, 2) if not np.isnan(mkt_y) else None,
        "excess_pct": round((strat_y - mkt_y) * 100, 2) if not np.isnan(mkt_y) else None,
        "avg_win": round(float(g["win_count"].mean()), 1),
    })

# 全期累计
strat_total = compound(pdf["strat_ret"].values)
mkt_total = compound(pdf["mkt_median_ret"].dropna().values)

out = {"yearly": yearly,
       "total": {"strat_return_pct": round(strat_total*100,2),
                 "mkt_median_return_pct": round(mkt_total*100,2),
                 "excess_pct": round((strat_total-mkt_total)*100,2)}}
json.dump(out, open(BASE + "/yearly_summary_result.json","w"), ensure_ascii=False, indent=2)

print("\n" + "="*68)
print("%-6s%8s%14s%16s%12s%8s" % ("年份","调仓期","策略收益","大盘中位收益","超额","均胜"))
print("-"*68)
for y in yearly:
    mm = "%+.2f%%" % y["mkt_median_return_pct"] if y["mkt_median_return_pct"] is not None else "NA"
    ex = "%+.2f%%" % y["excess_pct"] if y["excess_pct"] is not None else "NA"
    print("%-6s%8d%13.2f%%%15s%12s%8.1f" % (y["year"], y["periods"], y["strat_return_pct"], mm, ex, y["avg_win"]))
print("-"*68)
print("%-6s%8s%13.2f%%%15s%12s" % ("累计","",  out["total"]["strat_return_pct"], "%+.2f%%"%out["total"]["mkt_median_return_pct"], "%+.2f%%"%out["total"]["excess_pct"]))
print("="*68)
log("saved yearly_summary_result.json")
