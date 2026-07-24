"""
S014 超额收益归因诊断
问题：S014(Top1/Top2头部) 相对 S013主仓(Top20) 的超额收益，
      到底是"细水长流每期都有一点"，还是"全靠少数暴击期"？

方法：
  - 逐期计算 S014头部收益 - S013主仓(Top20)收益 = 超额
  - 看超额的时间分布：正超额期占比、超额的集中度(前N期贡献多少)、逐年超额
  - 若超额高度集中在少数期 → 择时开关(路C)有意义
  - 若超额均匀分布 → 更适合结构改造(路A/B)
"""
import ast, csv
import pandas as pd, numpy as np
from pathlib import Path

S013_CSV = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S013-喜神池LightGBM选股/trades_s013b.csv"
OUT = Path("/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S014-Top精选集中策略")
BC, SC = 0.00025, 0.00125

# 复用已算好的明细（含top1/top2/top3和original收益）
detail = pd.read_csv(OUT / "s014_top_detail_s013b.csv", encoding="utf-8-sig")
print(f"明细期数: {len(detail)}")
print("列:", list(detail.columns))

# 每期超额 = 头部收益 - 主仓(original=Top20)收益
detail["excess_top1"] = detail["top1_return"] - detail["original_return"]
detail["excess_top2"] = detail["top2_return"] - detail["original_return"]
detail["year"] = detail["sell_date"].astype(str).str[:4]

def attribute(excess_col, name):
    s = detail[excess_col].dropna()
    n = len(s)
    total_excess = s.sum()
    print(f"\n{'='*60}\n  {name} 超额收益归因 (共{n}期)\n{'='*60}")
    print(f"  累计超额(算术和): {total_excess*100:+.1f}pp")
    print(f"  正超额期数: {(s>0).sum()}/{n} = {(s>0).mean()*100:.1f}%")
    print(f"  单期超额 均值{s.mean()*100:+.2f}% 中位{s.median()*100:+.2f}%")

    # 集中度：按超额绝对贡献排序，看前N期占比
    sorted_ex = s.sort_values(ascending=False)
    for topk in [3, 5, 10]:
        contrib = sorted_ex.head(topk).sum()
        print(f"  超额最高{topk}期 贡献: {contrib*100:+.1f}pp ({contrib/total_excess*100:.0f}% of 累计超额)" if total_excess>0 else f"  top{topk}期: {contrib*100:+.1f}pp")

    # 若去掉最猛的3期，还剩多少
    remain = sorted_ex.iloc[3:].sum()
    print(f"  剔除最猛3期后 剩余累计超额: {remain*100:+.1f}pp")

    # 逐年超额
    print(f"\n  逐年超额(算术和):")
    yr = detail.groupby("year")[excess_col].sum() * 100
    yr_pos = detail.groupby("year")[excess_col].apply(lambda x:(x.dropna()>0).mean()*100)
    for y in yr.index:
        print(f"    {y}: {yr[y]:+6.1f}pp  (正超额期占比{yr_pos[y]:.0f}%)")
    return s, sorted_ex

s1, sorted1 = attribute("excess_top1", "Top1 vs 主仓")
s2, sorted2 = attribute("excess_top2", "Top2 vs 主仓")

# 保存逐期超额供画图
out = detail[["sell_date","year","original_return","top1_return","top2_return","excess_top1","excess_top2"]].copy()
out.to_csv(OUT / "s014_excess_detail.csv", index=False, encoding="utf-8-sig")
print("\n已保存 s014_excess_detail.csv")
