#!/usr/bin/env python3
"""S009 月度策略组合 vs 沪深300 vs 上证指数 收益对比表"""
import pandas as pd
import akshare as ak
import numpy as np

BASE_DIR = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股"

# ── 1. 加载策略月度收益 ──
trades = pd.read_csv(f"{BASE_DIR}/trades_full.csv")
trades['ym'] = pd.to_datetime(trades['sell_date'], format='%Y%m%d').dt.to_period('M')
strategy = trades.set_index('ym')[['period_return']].rename(columns={'period_return': 'strategy'})

# ── 2. 沪深300 月收益 ──
hs300 = ak.stock_zh_index_daily(symbol='sh000300')
hs300['date'] = pd.to_datetime(hs300['date'])
hs300['ym'] = hs300['date'].dt.to_period('M')
hs300_m = hs300.sort_values('date').groupby('ym')['close'].last()
hs300_m = hs300_m.pct_change().dropna()

# ── 3. 上证指数 月收益 ──
sz = ak.stock_zh_index_daily(symbol='sh000001')
sz['date'] = pd.to_datetime(sz['date'])
sz['ym'] = sz['date'].dt.to_period('M')
sz_m = sz.sort_values('date').groupby('ym')['close'].last()
sz_m = sz_m.pct_change().dropna()

# ── 4. 合并 ──
df = pd.DataFrame({'hs300': hs300_m, 'shanghai': sz_m})
df['strategy'] = strategy['strategy']
df = df[df.index >= '2017-01'].dropna(subset=['strategy'])

# ── 5. 输出对比表(含年度汇总) ──
df['strategy_cum'] = (1 + df['strategy']).cumprod()
df['hs300_cum'] = (1 + df['hs300']).cumprod()
df['shanghai_cum'] = (1 + df['shanghai']).cumprod()

def fmt(x):
    return f"{x*100:+.2f}%"

print(f"{'月份':<10} {'策略组合':>10} {'沪深300':>10} {'上证指数':>10}  |  {'策略净值':>10} {'HS300净值':>10} {'上证净值':>10}")
print("=" * 85)

for i, row in df.iterrows():
    ym_str = str(i)
    print(f"{ym_str:<10} {fmt(row['strategy']):>10} {fmt(row['hs300']):>10} {fmt(row['shanghai']):>10}  |  {row['strategy_cum']:>10.2f} {row['hs300_cum']:>10.2f} {row['shanghai_cum']:>10.2f}")

# ── 年度汇总 ──
print("\n" + "=" * 85)
print(f"{'年份':<10} {'策略年收益':>12} {'HS300年收益':>12} {'上证年收益':>12}")
print("-" * 50)
for yr, sub in df.groupby(df.index.year):
    s_yr = (1 + sub['strategy']).prod() - 1
    h_yr = (1 + sub['hs300']).prod() - 1
    z_yr = (1 + sub['shanghai']).prod() - 1
    print(f"{yr:<10} {fmt(s_yr):>12} {fmt(h_yr):>12} {fmt(z_yr):>12}")

# ── 全期汇总 ──
print("-" * 50)
s_total = (1 + df['strategy']).prod() - 1
h_total = (1 + df['hs300']).prod() - 1
z_total = (1 + df['shanghai']).prod() - 1
n_yrs = len(df) / 12
s_ann = (1 + s_total) ** (1 / n_yrs) - 1
h_ann = (1 + h_total) ** (1 / n_yrs) - 1
z_ann = (1 + z_total) ** (1 / n_yrs) - 1
print(f"{'全期':<10} {fmt(s_total):>12} {fmt(h_total):>12} {fmt(z_total):>12}")
print(f"{'年化':<10} {fmt(s_ann):>12} {fmt(h_ann):>12} {fmt(z_ann):>12}")

# ── 写入CSV供查看 ──
df_out = df[['strategy','hs300','shanghai','strategy_cum','hs300_cum','shanghai_cum']].copy()
for c in ['strategy','hs300','shanghai']:
    df_out[c] = (df_out[c] * 100).round(2)
for c in ['strategy_cum','hs300_cum','shanghai_cum']:
    df_out[c] = df_out[c].round(4)
df_out.to_csv(f"{BASE_DIR}/monthly_comparison.csv", encoding='utf-8-sig')
print(f"\nCSV已写出: {BASE_DIR}/monthly_comparison.csv")
