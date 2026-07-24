#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S012 五行月份择时分析（基于S009已有回测结果 trades_full.csv）

用《五行属性对照表_2006-2046.xlsx·月份五行属性表》的节气划分，
给S009每期调仓打上五行标签，输出两版结果：
  版本A（不考虑空仓）：每月都调仓满仓，只按旺月/弱月分组统计收益差异
  版本B（考虑空仓）：只在旺月(木火水)持仓，弱月(金土)空仓持现金(年化3%)

五行月份（按节气，公历近似取每月节气日）：
  木: 寅月2/4-3/5, 卯月3/6-4/4       → 旺(持仓)
  土: 辰月4/5-5/5                     → 弱(空仓)
  火: 巳月5/6-6/5, 午月6/6-7/6       → 旺(持仓)
  土: 未月7/7-8/7                     → 弱(空仓)
  金: 申月8/8-9/7, 酉月9/8-10/7      → 弱(空仓)
  土: 戌月10/8-11/7                   → 弱(空仓)
  水: 亥月11/8-12/6, 子月12/7-1/5    → 旺(持仓)
  土: 丑月1/6-2/3                     → 弱(空仓)
"""
import pandas as pd, numpy as np

BASE = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股"
CASH_ANNUAL = 0.03  # 空仓月短债年化

def get_wuxing(date_str):
    """输入 YYYYMMDD，返回 (五行, 地支月, 是否旺月)。按节气边界判定。"""
    d = str(date_str)
    mm = int(d[4:6]); dd = int(d[6:8])
    md = mm * 100 + dd  # 月*100+日，便于区间比较

    # 各节气月边界（用 md 表示），跨年的子/丑月单独处理
    # 寅月 2/4-3/5
    if   204 <= md <= 305: return ("木", "寅", True)
    elif 306 <= md <= 404: return ("木", "卯", True)
    elif 405 <= md <= 505: return ("土", "辰", False)
    elif 506 <= md <= 605: return ("火", "巳", True)
    elif 606 <= md <= 706: return ("火", "午", True)
    elif 707 <= md <= 807: return ("土", "未", False)
    elif 808 <= md <= 907: return ("金", "申", False)
    elif 908 <= md <= 1007: return ("金", "酉", False)
    elif 1008 <= md <= 1107: return ("土", "戌", False)
    elif 1108 <= md <= 1206: return ("水", "亥", True)
    elif md >= 1207: return ("水", "子", True)          # 12/7-12/31
    elif md <= 105: return ("水", "子", True)            # 1/1-1/5
    elif 106 <= md <= 203: return ("土", "丑", False)    # 1/6-2/3
    else: return ("?", "?", False)

def main():
    t = pd.read_csv(f"{BASE}/trades_full.csv")
    t["year"] = t["rebalance_date"].astype(str).str[:4].astype(int)

    # 按买入日判定五行（买入日才是真正建仓的时点）
    wx = t["buy_date"].apply(get_wuxing)
    t["wuxing"]  = [w[0] for w in wx]
    t["dizhi"]   = [w[1] for w in wx]
    t["is_wang"] = [w[2] for w in wx]   # 旺月True

    print("="*70)
    print("S012 五行月份择时分析（基于S009的113期回测）")
    print("="*70)

    # ---- 每期五行标签一览 ----
    print("\n【每期五行标签】")
    print(f"{'调仓日':<10}{'买入日':<10}{'五行':<5}{'地支':<5}{'旺/弱':<6}{'收益':>9}")
    for _, r in t.iterrows():
        tag = "旺✓" if r["is_wang"] else "弱✗"
        print(f"{r['rebalance_date']:<10}{r['buy_date']:<11}{r['wuxing']:<5}{r['dizhi']:<5}{tag:<7}{r['period_return']*100:>+7.2f}%")

    # ==================== 版本A：不考虑空仓，只分组统计 ====================
    print("\n" + "="*70)
    print("【版本A】不考虑空仓（全程满仓每月调仓）—— 旺月vs弱月收益对比")
    print("="*70)

    wang = t[t["is_wang"]]
    weak = t[~t["is_wang"]]

    def stats(df, label):
        n = len(df)
        mean_r = df["period_return"].mean()*100
        median_r = df["period_return"].median()*100
        win = (df["period_return"]>0).sum()
        cum = (np.prod(1+df["period_return"].values)-1)*100
        print(f"  {label}: {n}期  平均{mean_r:+.2f}%  中位{median_r:+.2f}%  胜率{win}/{n}({win/n*100:.0f}%)  累计乘积{cum:+.1f}%")
        return mean_r, cum

    wm, wc = stats(wang, "旺月(木火水)")
    km, kc = stats(weak, "弱月(金土)  ")
    print(f"\n  → 旺月平均月收益 {wm:+.2f}% vs 弱月 {km:+.2f}%，差异 {wm-km:+.2f}个百分点")
    if wm > km:
        print(f"  → 旺月确实跑赢弱月，五行月份有区分度 ✓")
    else:
        print(f"  → 旺月没跑赢弱月，五行月份择时逻辑存疑 ✗")

    # 按五行细分
    print("\n  各五行分组：")
    for wx_name in ["木","火","水","土","金"]:
        sub = t[t["wuxing"]==wx_name]
        if len(sub)==0: continue
        mean_r = sub["period_return"].mean()*100
        win = (sub["period_return"]>0).sum()
        print(f"    {wx_name}: {len(sub)}期  平均{mean_r:+.2f}%  胜率{win}/{len(sub)}")

    # ==================== 版本B：考虑空仓 ====================
    print("\n" + "="*70)
    print("【版本B】考虑空仓（只在旺月持仓，弱月空仓持现金年化3%）")
    print("="*70)

    cash_monthly = (1+CASH_ANNUAL)**(1/12)-1  # 月度现金收益

    # 构造月度收益序列：旺月用策略收益，弱月用现金收益
    t_sorted = t.sort_values("rebalance_date").reset_index(drop=True)
    navA = 1.0  # 版本A: 全程满仓
    navB = 1.0  # 版本B: 旺月满仓弱月现金
    navB_hold = 1.0  # 对照: 弱月也满仓(就是A)
    curveA=[1.0]; curveB=[1.0]
    for _, r in t_sorted.iterrows():
        navA *= (1+r["period_return"])
        if r["is_wang"]:
            navB *= (1+r["period_return"])
        else:
            navB *= (1+cash_monthly)
        curveA.append(navA); curveB.append(navB)

    def perf(curve, label):
        arr = np.array(curve)
        total = (arr[-1]-1)*100
        n_months = len(arr)-1
        years = n_months/12
        ann = (arr[-1]**(1/years)-1)*100 if years>0 else 0
        # 最大回撤
        peak = np.maximum.accumulate(arr)
        dd = (arr-peak)/peak
        mdd = dd.min()*100
        print(f"  {label}: 累计{total:+.1f}%  年化{ann:+.2f}%  最大回撤{mdd:.1f}%")
        return total, ann, mdd

    print(f"\n  期数: 总{len(t)}期, 旺月{len(wang)}期(持仓), 弱月{len(weak)}期(空仓)")
    print()
    tA,aA,dA = perf(curveA, "版本A 全程满仓(=原S009) ")
    tB,aB,dB = perf(curveB, "版本B 旺月持仓弱月空仓   ")
    print()
    print(f"  → 空仓避开弱月后:")
    print(f"     年化 {aA:.1f}% → {aB:.1f}% ({'降' if aB<aA else '升'}{abs(aB-aA):.1f}个点)")
    print(f"     回撤 {dA:.1f}% → {dB:.1f}% ({'改善' if dB>dA else '恶化'}{abs(dB-dA):.1f}个点)")
    print(f"     累计 {tA:+.0f}% → {tB:+.0f}%")

    # 保存
    out = {
        "version_A_no_cash": {
            "wang_mean_pct": round(wm,2), "weak_mean_pct": round(km,2),
            "wang_periods": len(wang), "weak_periods": len(weak),
            "wang_cum_pct": round(wc,1), "weak_cum_pct": round(kc,1),
        },
        "version_B_with_cash": {
            "A_total_pct": round(tA,1), "A_annual_pct": round(aA,2), "A_mdd_pct": round(dA,1),
            "B_total_pct": round(tB,1), "B_annual_pct": round(aB,2), "B_mdd_pct": round(dB,1),
        },
        "per_period": t[["rebalance_date","buy_date","wuxing","dizhi","is_wang","period_return"]].to_dict("records"),
    }
    import json
    json.dump(out, open("wuxing_month_result.json","w"), ensure_ascii=False, indent=2, default=str)
    print("\n结果已保存: wuxing_month_result.json")

if __name__ == "__main__":
    main()
