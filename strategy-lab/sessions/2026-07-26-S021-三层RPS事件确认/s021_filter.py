#!/usr/bin/env python3
"""
S021 组合过滤验证
=================
在已生成的 s021_events.csv 基础上，测试多层过滤条件对翻倍率的提升。
核心问题：风口龙头 × 第5天高stock_rps × 正斜率，翻倍率能否显著跑赢2.5%基准？

方法：不重跑数据，直接用第5天确认点(confirm_day==5)的三层RPS做条件筛选。
每个事件是唯一的(ts_code+t0_date)，第5天是最强确认点。
"""
import pandas as pd, numpy as np

CSV = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-26-S021-三层RPS事件确认/s021_events.csv"
df = pd.read_csv(CSV)

# 用第5天确认点(区分力最强)
d5 = df[df['confirm_day'] == 5].copy()
BASE = (d5['label'] == 2).mean() * 100
N_TOTAL = len(d5)
print("="*70)
print("S021 组合过滤验证 (确认点=T0后第5天)")
print("="*70)
print(f"总事件数: {N_TOTAL}  |  基准翻倍率: {BASE:.2f}%\n")

def evalf(name, mask):
    s = d5[mask]
    n = len(s)
    if n == 0:
        print(f"{name:<48} n=0"); return
    dbl = (s['label'] == 2).sum()
    half = (s['label'] >= 1).sum()  # 翻倍+50%都算
    dbl_rate = dbl / n * 100
    half_rate = half / n * 100
    lift = dbl_rate / BASE
    print(f"{name:<48} n={n:>6}  翻倍率={dbl_rate:>5.2f}% (×{lift:.2f})  ≥50%率={half_rate:>5.2f}%")

print("--- 单条件 ---")
evalf("① 风口龙头", d5['quadrant'] == '风口龙头')
evalf("② stock_rps > 85", d5['stock_rps'] > 85)
evalf("③ stock_rps > 90", d5['stock_rps'] > 90)
evalf("④ 斜率 > 0 (RPS仍在走高)", d5['stock_rps_slope5'] > 0)
evalf("⑤ 斜率 > 3", d5['stock_rps_slope5'] > 3)
evalf("⑥ rel_rps > 0 (个股跑赢板块)", d5['rel_rps'] > 0)

print("\n--- 双条件组合 ---")
evalf("风口龙头 + stock_rps>85", (d5['quadrant']=='风口龙头') & (d5['stock_rps']>85))
evalf("风口龙头 + 斜率>0", (d5['quadrant']=='风口龙头') & (d5['stock_rps_slope5']>0))
evalf("stock_rps>85 + 斜率>0", (d5['stock_rps']>85) & (d5['stock_rps_slope5']>0))
evalf("stock_rps>90 + 斜率>3", (d5['stock_rps']>90) & (d5['stock_rps_slope5']>3))

print("\n--- 三条件组合 (核心假设) ---")
evalf("风口龙头 + stock_rps>85 + 斜率>0",
      (d5['quadrant']=='风口龙头') & (d5['stock_rps']>85) & (d5['stock_rps_slope5']>0))
evalf("风口龙头 + stock_rps>90 + 斜率>0",
      (d5['quadrant']=='风口龙头') & (d5['stock_rps']>90) & (d5['stock_rps_slope5']>0))
evalf("风口龙头 + stock_rps>90 + 斜率>3",
      (d5['quadrant']=='风口龙头') & (d5['stock_rps']>90) & (d5['stock_rps_slope5']>3))

print("\n--- 极端过滤 (追求最高翻倍率) ---")
evalf("风口龙头 + rps>90 + 斜率>3 + rel>0",
      (d5['quadrant']=='风口龙头') & (d5['stock_rps']>90) & (d5['stock_rps_slope5']>3) & (d5['rel_rps']>0))
evalf("rps>95 + 斜率>5",
      (d5['stock_rps']>95) & (d5['stock_rps_slope5']>5))
evalf("rps>95 + 斜率>5 + 板块rps>90",
      (d5['stock_rps']>95) & (d5['stock_rps_slope5']>5) & (d5['sector_rps']>90))

# ============ 网格扫描: 找最优阈值组合 ============
print("\n" + "="*70)
print("网格扫描: stock_rps阈值 × 斜率阈值 (限风口龙头) → 翻倍率热力")
print("="*70)
rps_grid = [80, 85, 90, 95]
slope_grid = [0, 3, 5, 8]
wind = d5[d5['quadrant'] == '风口龙头']
print(f"{'rps\\斜率':<10}" + "".join(f"{s:>14}" for s in slope_grid))
for r in rps_grid:
    line = f"rps>{r:<6}"
    for sl in slope_grid:
        s = wind[(wind['stock_rps']>r) & (wind['stock_rps_slope5']>sl)]
        if len(s) < 30:
            line += f"{'n<30':>14}"
        else:
            rate = (s['label']==2).mean()*100
            line += f"{rate:>7.1f}%(n{len(s)})"[:14].rjust(14)
    print(line)

print(f"\n提示: 翻倍率需显著>{BASE:.1f}%(基准)且样本足够(n>50)才有实战价值")
