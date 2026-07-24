#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S012 实验一 · 五行月份仓位加权回测（温和版，不清仓、不打断复利）

背景：首轮已证明"弱月清仓"是负优化（五行是强度信号，不是空仓开关）。
本实验改为"仓位系数加权"——旺月加仓/满仓，弱月减仓但不清仓，未投资部分持现金(年化3%)。

收益模型（单期）：
    仓位系数 w ∈ (0, 1] 无杠杆 / >1 带杠杆
    加权期收益 = w * period_return + (1-w) * cash_monthly   (无杠杆，1-w部分持现金)
    带杠杆(w>1)：加权期收益 = w * period_return - (w-1)*margin_monthly (超出部分按融资成本计息)

无未来函数：仓位系数只依赖"当期买入日属于哪个五行月"，实盘当天即可确定。

水月统一按旺月处理（老大决策：尊重原五行框架，水不单独降级）。
"""
import pandas as pd, numpy as np, json

BASE = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股"
CASH_ANNUAL = 0.03      # 空闲资金短债年化
MARGIN_ANNUAL = 0.06    # 融资年化成本（带杠杆时超出100%部分的利息）
CASH_M = (1 + CASH_ANNUAL) ** (1/12) - 1
MARGIN_M = (1 + MARGIN_ANNUAL) ** (1/12) - 1


def get_wuxing(date_str):
    """输入 YYYYMMDD，返回 (五行, 地支月, 是否旺月)。按节气边界判定。"""
    d = str(date_str)
    mm = int(d[4:6]); dd = int(d[6:8])
    md = mm * 100 + dd
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
    elif md >= 1207: return ("水", "子", True)
    elif md <= 105: return ("水", "子", True)
    elif 106 <= md <= 203: return ("土", "丑", False)
    else: return ("?", "?", False)


def weighted_return(period_ret, w):
    """按仓位系数 w 计算加权期收益。无杠杆(w<=1)剩余持现金；带杠杆(w>1)超出部分付融资成本。"""
    if w <= 1.0:
        return w * period_ret + (1 - w) * CASH_M
    else:
        return w * period_ret - (w - 1) * MARGIN_M


def perf(period_rets):
    """输入期收益序列，返回累计/年化/最大回撤/夏普/胜率。"""
    pr = np.array(period_rets, dtype=float)
    nav = np.cumprod(1 + pr)
    nav_full = np.concatenate([[1.0], nav])
    total = nav_full[-1] - 1
    n = len(pr)
    years = n / 12.0
    ann = nav_full[-1] ** (1/years) - 1 if years > 0 and nav_full[-1] > 0 else 0
    peak = np.maximum.accumulate(nav_full)
    mdd = ((nav_full - peak) / peak).min()
    sharpe = pr.mean() / pr.std() * np.sqrt(12) if n > 1 and pr.std() > 0 else 0
    win = (pr > 0).mean()
    return {
        "total_pct": round(total * 100, 1),
        "annual_pct": round(ann * 100, 2),
        "mdd_pct": round(mdd * 100, 1),
        "sharpe": round(float(sharpe), 3),
        "win_pct": round(float(win) * 100, 1),
        "nav_curve": [round(float(x), 6) for x in nav_full],
    }


def main():
    t = pd.read_csv(f"{BASE}/trades_full.csv").sort_values("rebalance_date").reset_index(drop=True)
    wx = t["buy_date"].apply(get_wuxing)
    t["wuxing"]  = [w[0] for w in wx]
    t["dizhi"]   = [w[1] for w in wx]
    t["is_wang"] = [w[2] for w in wx]

    dates = t["rebalance_date"].astype(str).tolist()
    base_rets = t["period_return"].values

    # ============ 方案定义 ============
    # 每个方案是一个 { 五行: 仓位系数 } 的映射（缺省1.0）
    schemes = {
        "基线(=S009原版)":      {"木":1.0, "火":1.0, "水":1.0, "金":1.0, "土":1.0},
        # —— 无杠杆组（可实盘，总仓位≤100%）——
        "无杠杆·旺1.0弱0.8":    {"木":1.0, "火":1.0, "水":1.0, "金":0.8, "土":0.8},
        "无杠杆·旺1.0弱0.7":    {"木":1.0, "火":1.0, "水":1.0, "金":0.7, "土":0.7},
        "无杠杆·旺1.0弱0.6":    {"木":1.0, "火":1.0, "水":1.0, "金":0.6, "土":0.6},
        "无杠杆·分档(木1.0火0.95水0.9金0.75土0.7)": {"木":1.0,"火":0.95,"水":0.9,"金":0.75,"土":0.7},
        # —— 带杠杆组（研究上限参考，旺月加仓）——
        "带杠杆·旺1.2弱0.8":    {"木":1.2, "火":1.2, "水":1.2, "金":0.8, "土":0.8},
        "带杠杆·旺1.3弱0.7":    {"木":1.3, "火":1.3, "水":1.3, "金":0.7, "土":0.7},
        "带杠杆·分档(木1.3火1.2水1.1金0.8土0.7)": {"木":1.3,"火":1.2,"水":1.1,"金":0.8,"土":0.7},
    }

    print("="*78)
    print("S012 实验一 · 五行月份仓位加权回测（113期，复用S009真实收益）")
    print("="*78)
    print(f"现金月收益 {CASH_M*100:.3f}%  融资月成本 {MARGIN_M*100:.3f}%\n")

    results = {}
    base_perf = None
    for name, wmap in schemes.items():
        rets = []
        for i, r in t.iterrows():
            w = wmap.get(r["wuxing"], 1.0)
            rets.append(weighted_return(base_rets[i], w))
        p = perf(rets)
        results[name] = {"weights": wmap, **{k: v for k, v in p.items() if k != "nav_curve"},
                         "nav_curve": p["nav_curve"]}
        if name.startswith("基线"):
            base_perf = p

    # ============ 打印对比表 ============
    print(f"{'方案':<42}{'累计%':>9}{'年化%':>8}{'回撤%':>8}{'夏普':>7}{'胜率%':>7}")
    print("-"*82)
    for name, r in results.items():
        d_ann = r["annual_pct"] - base_perf["annual_pct"]
        d_mdd = r["mdd_pct"] - base_perf["mdd_pct"]
        flag = ""
        if not name.startswith("基线"):
            # 年化更高 且 回撤不恶化(更接近0/更大) 才算改善
            if r["annual_pct"] >= base_perf["annual_pct"] and r["mdd_pct"] >= base_perf["mdd_pct"]:
                flag = "  ✓帕累托改善"
            elif r["annual_pct"] < base_perf["annual_pct"] and r["mdd_pct"] < base_perf["mdd_pct"]:
                flag = "  ✗双输"
        print(f"{name:<42}{r['total_pct']:>9.0f}{r['annual_pct']:>8.2f}{r['mdd_pct']:>8.1f}"
              f"{r['sharpe']:>7.3f}{r['win_pct']:>7.1f}{flag}")

    print("\n【判读】")
    print("  · 帕累托改善 = 年化不降 且 回撤不恶化，是真正的免费午餐")
    print("  · 若所有加权方案都无法帕累托改善基线，说明月份择时连'温和加权'也难增益")

    # 保存
    out = {
        "meta": {
            "n_periods": len(t),
            "cash_annual": CASH_ANNUAL, "margin_annual": MARGIN_ANNUAL,
            "date_start": dates[0], "date_end": dates[-1],
        },
        "dates": dates,
        "schemes": results,
    }
    json.dump(out, open("wuxing_weight_result.json", "w"), ensure_ascii=False, indent=2, default=str)
    print("\n结果已保存: wuxing_weight_result.json")


if __name__ == "__main__":
    main()
