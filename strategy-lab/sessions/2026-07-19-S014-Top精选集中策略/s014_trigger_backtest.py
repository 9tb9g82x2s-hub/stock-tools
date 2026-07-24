"""
S014 路C - 触发A：S013主仓动量开关回测
思路：上一期(或近N期)S013主仓收益满足条件时，才开启本期Top1/Top2跟投
      否则本期不跟（空仓）

测试多种触发条件：
  - 上1期主仓 > 0（上期正收益就跟）
  - 上1期主仓 > X%（设定阈值）
  - 上2期主仓均值 > 0
  - 上3期主仓均值 > 0
  - 上1期主仓 > 0 且 上期Top1也>0（双重确认）
"""
import ast, csv
import pandas as pd, numpy as np
from pathlib import Path

OUT = Path("/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S014-Top精选集中策略")
detail = pd.read_csv(OUT / "s014_top_detail_s013b.csv", encoding="utf-8-sig")
detail = detail.sort_values("sell_date").reset_index(drop=True)
detail["year"] = detail["sell_date"].astype(str).str[:4]

# 主仓收益序列（滞后1期 = 上期表现）
detail["lag1_orig"]  = detail["original_return"].shift(1)
detail["lag2_orig"]  = detail["original_return"].shift(2)
detail["lag3_orig"]  = detail["original_return"].shift(3)
detail["lag1_top1"]  = detail["top1_return"].shift(1)
detail["roll2_orig"] = (detail["lag1_orig"] + detail["lag2_orig"]) / 2
detail["roll3_orig"] = (detail["lag1_orig"] + detail["lag2_orig"] + detail["lag3_orig"]) / 3

N_YEARS = len(detail) / 12

def backtest_trigger(trigger_mask, invest_col, name):
    """
    trigger_mask: bool Series，True表示本期开启跟投
    invest_col: 'top1_return' or 'top2_return'
    """
    rets = []
    for i, row in detail.iterrows():
        if pd.isna(trigger_mask.iloc[i]) or not trigger_mask.iloc[i]:
            rets.append(0.0)  # 不跟投，收益=0（空仓）
        else:
            r = row[invest_col]
            rets.append(r if not pd.isna(r) else 0.0)
    s = pd.Series(rets)
    nav = (1 + s).cumprod()
    total = nav.iloc[-1] - 1
    annual = (1 + total) ** (1 / N_YEARS) - 1
    dd = ((nav - nav.cummax()) / nav.cummax()).min()
    active = trigger_mask.sum()
    sharpe = s[s != 0].mean() / s[s != 0].std() * (12**0.5) if s[s!=0].std() > 0 else 0
    # 只在跟投期计算胜率
    active_rets = s[trigger_mask.fillna(False) & (s != 0)]
    win_rate = (active_rets > 0).mean() if len(active_rets) > 0 else 0
    return {
        "name": name,
        "col": invest_col,
        "annual": round(annual * 100, 1),
        "total": round(total * 100, 1),
        "max_dd": round(dd * 100, 1),
        "sharpe_active": round(sharpe, 2),
        "win_rate": round(win_rate * 100, 1),
        "active_n": int(active),
        "active_pct": round(active / len(detail) * 100, 1),
    }

# 基准：常设（全程跟投）
BASE_T1 = detail["top1_return"].notna()
BASE_T2 = detail["top2_return"].notna()

triggers = {
    "上1期>0":       detail["lag1_orig"] > 0,
    "上1期>1%":      detail["lag1_orig"] > 0.01,
    "上1期>2%":      detail["lag1_orig"] > 0.02,
    "上2期均>0":     detail["roll2_orig"] > 0,
    "上3期均>0":     detail["roll3_orig"] > 0,
    "上1期>0且Top1上期>0": (detail["lag1_orig"] > 0) & (detail["lag1_top1"] > 0),
}

results = []
# 全程常设基准
results.append(backtest_trigger(BASE_T1, "top1_return", "★ 全程常设（基准）"))
results.append(backtest_trigger(BASE_T2, "top2_return", "★ 全程常设（基准）"))
for tname, tmask in triggers.items():
    results.append(backtest_trigger(tmask, "top1_return", tname))
    results.append(backtest_trigger(tmask, "top2_return", tname))

df_r = pd.DataFrame(results)

print("=" * 75)
print("  S014 触发A 动量开关 回测结果（S013b 源）")
print("=" * 75)
print(f"\n  {'触发条件':<22} {'投入列':<9} {'年化':>7} {'回撤':>7} {'夏普':>6} {'开仓%':>7}")
print(f"  {'-'*22} {'-'*9} {'-'*7} {'-'*7} {'-'*6} {'-'*7}")

# S013主仓对照
s_main = detail["original_return"]
nav_m = (1 + s_main).cumprod()
tot_m = nav_m.iloc[-1] - 1
ann_m = (1 + tot_m) ** (1 / N_YEARS) - 1
dd_m = ((nav_m - nav_m.cummax()) / nav_m.cummax()).min()
print(f"  {'[参照] S013主仓Top20':<22} {'—':<9} {ann_m*100:>6.1f}% {dd_m*100:>6.1f}% {'1.44':>6} {'100%':>7}")
print()

for _, r in df_r.iterrows():
    col_label = "Top1" if r["col"] == "top1_return" else "Top2"
    print(f"  {r['name']:<22} {col_label:<9} {r['annual']:>6.1f}% {r['max_dd']:>6.1f}% {r['sharpe_active']:>6.2f} {r['active_pct']:>6.1f}%")

# ── 重点看超额 ──
print("\n\n  对比超额（vs S013主仓年化36.6%）:")
print(f"  {'触发条件':<22} {'投入列':<9} {'超额':>7} {'回撤差':>8}")
for _, r in df_r.iterrows():
    col_label = "Top1" if r["col"] == "top1_return" else "Top2"
    excess = r["annual"] - ann_m * 100
    dd_diff = r["max_dd"] - dd_m * 100
    marker = " <<<"  if (excess > 5 and r["max_dd"] > -35) else ""
    print(f"  {r['name']:<22} {col_label:<9} {excess:>+6.1f}pp {dd_diff:>+7.1f}pp{marker}")

df_r.to_csv(OUT / "s014_trigger_result.csv", index=False, encoding="utf-8-sig")
print(f"\n结果已保存: s014_trigger_result.csv")
