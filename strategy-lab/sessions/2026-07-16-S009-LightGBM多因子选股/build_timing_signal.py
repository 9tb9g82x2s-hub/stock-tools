#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v1.4 择时信号构建：
用全市场每日收盘价中位数构造一条"市场指数"，算其20日均线(MA20)。
每个交易日的信号 = 市场中位数价 是否 >= 其MA20。
严格无未来函数：MA20只用当日及之前的数据。
输出 timing_signal.json: {trade_date: {mkt: 中位数, ma20: 均线, bull: True/False}}
"""
import sqlite3, json, time
import pandas as pd, numpy as np

DB = "/Users/ziruzhu/stock-data/stock_all.db"
BASE = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股"

def log(m): print("[%s] %s" % (time.strftime('%H:%M:%S'), m), flush=True)

con = sqlite3.connect(DB)
log("读全市场日线收盘价...")
# 用 pct_chg 更稳，但中位数价格更直观；这里用每日全市场 close 中位数
df = pd.read_sql("SELECT trade_date, close FROM daily WHERE trade_date >= '20151201'", con)
con.close()
df["close"] = pd.to_numeric(df["close"], errors="coerce")
df = df.dropna(subset=["close"])
df = df[df["close"] > 0]

log("按日聚合中位数...")
mkt = df.groupby("trade_date")["close"].median().reset_index()
mkt.columns = ["trade_date", "mkt_median"]
mkt = mkt.sort_values("trade_date").reset_index(drop=True)

# 20日均线（只用历史，rolling自然不含未来）
mkt["ma20"] = mkt["mkt_median"].rolling(20, min_periods=20).mean()
mkt["bull"] = mkt["mkt_median"] >= mkt["ma20"]

# 输出
sig = {}
for _, r in mkt.iterrows():
    if pd.isna(r["ma20"]):
        continue
    sig[str(r["trade_date"])] = {
        "mkt": round(float(r["mkt_median"]), 3),
        "ma20": round(float(r["ma20"]), 3),
        "bull": bool(r["bull"]),
    }
json.dump(sig, open(f"{BASE}/timing_signal.json", "w"), ensure_ascii=False)
log("信号覆盖 %d 个交易日" % len(sig))

# 统计各年 bull 比例
mkt2 = mkt.dropna(subset=["ma20"]).copy()
mkt2["year"] = mkt2["trade_date"].str[:4]
print("\n各年市场处于均线上方(bull)的交易日占比:")
for yr, g in mkt2.groupby("year"):
    if yr < "2017": continue
    print("  %s: %5.1f%% (共%d日)" % (yr, g["bull"].mean()*100, len(g)))

# 看7月股灾附近的信号
print("\n2026年7月信号(股灾前后):")
recent = mkt[(mkt["trade_date"]>="20260701") & (mkt["trade_date"]<="20260717")]
for _, r in recent.iterrows():
    print("  %s  中位价%.2f  MA20 %.2f  %s" % (r["trade_date"], r["mkt_median"], r["ma20"], "多头" if r["bull"] else "空头★"))
