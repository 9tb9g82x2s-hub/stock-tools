#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S012 实验二 · 随机对照实验（蒙特卡洛）

问题：五行月份择时是真实信号，还是数据挖掘的伪规律？

方法：
  - 真实五行：旺月(木火水)=65期，弱月(金土)=48期
  - 随机对照：从113期中随机抽65期当"旺月"（等数量，纯随机）
  - 重复2000次，统计"随机旺月平均收益"的分布
  - 看真实五行旺月均收益落在随机分布的哪个分位
  - 若百分位 > 90% → 五行有统计显著性；< 90% → 可能是偶然

同时做两个维度的对照：
  A. 月收益均值（旺月平均月收益）
  B. 年化收益（旺月期间全仓持有的复利年化）
"""
import pandas as pd, numpy as np, json, time

BASE = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股"
N_SIM = 2000
SEED = 42


def get_wuxing(date_str):
    d = str(date_str)
    mm = int(d[4:6]); dd = int(d[6:8])
    md = mm * 100 + dd
    if   204 <= md <= 305: return ("木", True)
    elif 306 <= md <= 404: return ("木", True)
    elif 405 <= md <= 505: return ("土", False)
    elif 506 <= md <= 605: return ("火", True)
    elif 606 <= md <= 706: return ("火", True)
    elif 707 <= md <= 807: return ("土", False)
    elif 808 <= md <= 907: return ("金", False)
    elif 908 <= md <= 1007: return ("金", False)
    elif 1008 <= md <= 1107: return ("土", False)
    elif 1108 <= md <= 1206: return ("水", True)
    elif md >= 1207: return ("水", True)
    elif md <= 105: return ("水", True)
    elif 106 <= md <= 203: return ("土", False)
    else: return ("?", False)


def main():
    t0 = time.time()
    t = pd.read_csv(f"{BASE}/trades_full.csv").sort_values("rebalance_date").reset_index(drop=True)
    wx = t["buy_date"].apply(get_wuxing)
    t["wuxing"]  = [w[0] for w in wx]
    t["is_wang"] = [w[1] for w in wx]

    rets = t["period_return"].values
    n_total = len(rets)
    n_wang = t["is_wang"].sum()   # 真实旺月期数（65期）
    n_weak = n_total - n_wang

    # ——— 真实五行的统计量 ———
    wang_mask = t["is_wang"].values
    real_wang_mean = rets[wang_mask].mean()
    real_weak_mean = rets[~wang_mask].mean()
    real_diff = real_wang_mean - real_weak_mean

    # 真实五行旺月的"全仓持有年化"（65期，仅在旺月持仓，弱月按0%算）
    real_curve = []
    nav = 1.0
    for i, is_w in enumerate(wang_mask):
        if is_w:
            nav *= (1 + rets[i])
        real_curve.append(nav)
    real_nav_end = real_curve[-1]
    # 年化：旺月合计期数 / 12
    real_ann = real_nav_end ** (12 / n_wang) - 1

    print("="*66)
    print("S012 实验二 · 五行择时 vs 随机对照（蒙特卡洛 N=2000）")
    print("="*66)
    print(f"真实五行：总{n_total}期，旺月{n_wang}期，弱月{n_weak}期")
    print(f"真实旺月均月收益：{real_wang_mean*100:+.2f}%")
    print(f"真实弱月均月收益：{real_weak_mean*100:+.2f}%")
    print(f"真实旺-弱差：{real_diff*100:+.2f}个百分点")
    print(f"真实旺月年化(仅旺月全仓)：{real_ann*100:.2f}%\n")

    # ——— 蒙特卡洛模拟 ———
    rng = np.random.default_rng(SEED)
    sim_means = np.empty(N_SIM)
    sim_diffs = np.empty(N_SIM)
    sim_anns  = np.empty(N_SIM)

    for i in range(N_SIM):
        idx = rng.choice(n_total, size=n_wang, replace=False)
        sim_wang = rets[idx]
        sim_weak = np.delete(rets, idx)

        sim_means[i] = sim_wang.mean()
        sim_diffs[i] = sim_wang.mean() - sim_weak.mean()

        # 随机旺月的"全仓持有年化"
        sim_nav = np.prod(1 + sim_wang)
        sim_anns[i] = sim_nav ** (12 / n_wang) - 1

    # ——— 计算百分位 ———
    pct_mean = (sim_means < real_wang_mean).mean() * 100
    pct_diff = (sim_diffs < real_diff).mean() * 100
    pct_ann  = (sim_anns  < real_ann).mean() * 100

    print(f"随机对照分布（N={N_SIM}次，每次随机抽{n_wang}期作为'旺月'）：")
    print(f"")
    print(f"  【维度A：旺月均月收益】")
    print(f"     随机分布: 均值{sim_means.mean()*100:+.2f}%  "
          f"P10={np.percentile(sim_means,10)*100:+.2f}%  "
          f"P90={np.percentile(sim_means,90)*100:+.2f}%")
    print(f"     真实五行: {real_wang_mean*100:+.2f}%  ← 超过随机的 {pct_mean:.1f}% 模拟")
    if pct_mean >= 90:
        verdict_mean = f"✓ 显著（百分位{pct_mean:.1f}% ≥ 90%，五行旺月收益真实偏高）"
    elif pct_mean >= 70:
        verdict_mean = f"△ 较弱（百分位{pct_mean:.1f}%，偏高但不显著）"
    else:
        verdict_mean = f"✗ 不显著（百分位{pct_mean:.1f}%，与随机无差异）"
    print(f"     结论: {verdict_mean}")

    print(f"")
    print(f"  【维度B：旺-弱收益差】")
    print(f"     随机分布: 均值{sim_diffs.mean()*100:+.2f}%  "
          f"P10={np.percentile(sim_diffs,10)*100:+.2f}%  "
          f"P90={np.percentile(sim_diffs,90)*100:+.2f}%")
    print(f"     真实五行: {real_diff*100:+.2f}%  ← 超过随机的 {pct_diff:.1f}% 模拟")
    if pct_diff >= 90:
        verdict_diff = f"✓ 显著（百分位{pct_diff:.1f}% ≥ 90%，旺弱差异非偶然）"
    elif pct_diff >= 70:
        verdict_diff = f"△ 较弱（百分位{pct_diff:.1f}%，有方向但不显著）"
    else:
        verdict_diff = f"✗ 不显著（百分位{pct_diff:.1f}%，旺弱差与随机分组无区别）"
    print(f"     结论: {verdict_diff}")

    print(f"")
    print(f"  【维度C：旺月年化收益（仅旺月持仓复利）】")
    print(f"     随机分布: 均值{sim_anns.mean()*100:+.2f}%  "
          f"P10={np.percentile(sim_anns,10)*100:+.2f}%  "
          f"P90={np.percentile(sim_anns,90)*100:+.2f}%")
    print(f"     真实五行: {real_ann*100:.2f}%  ← 超过随机的 {pct_ann:.1f}% 模拟")
    if pct_ann >= 90:
        verdict_ann = f"✓ 显著（百分位{pct_ann:.1f}%）"
    elif pct_ann >= 70:
        verdict_ann = f"△ 较弱（百分位{pct_ann:.1f}%）"
    else:
        verdict_ann = f"✗ 不显著（百分位{pct_ann:.1f}%）"
    print(f"     结论: {verdict_ann}")

    print(f"")
    print(f"  【综合判断】")
    n_sig = sum(1 for p in [pct_mean, pct_diff, pct_ann] if p >= 90)
    if n_sig >= 2:
        overall = ("✓✓ 五行月份具有统计显著性（≥2个维度显著），"
                   "非纯粹的数据挖掘伪规律，有保留研究价值。")
    elif n_sig == 1:
        overall = ("△ 五行月份信号较弱（仅1个维度显著），"
                   "建议将其定性为'季节性效应'而非五行因果。")
    else:
        overall = ("✗ 五行月份无统计显著性（0个维度显著），"
                   "与随机择时无区别，'五行'标签是伪包装，应重新定性为纯季节性研究。")
    print(f"  {overall}")

    # 保存结果
    out = {
        "meta": {
            "n_total": int(n_total), "n_wang": int(n_wang), "n_weak": int(n_weak),
            "n_sim": N_SIM, "seed": SEED,
        },
        "real_wuxing": {
            "wang_mean_pct": round(real_wang_mean*100, 3),
            "weak_mean_pct": round(real_weak_mean*100, 3),
            "diff_pct": round(real_diff*100, 3),
            "ann_pct": round(real_ann*100, 2),
        },
        "simulation": {
            "mean_pct_dist": {
                "mean": round(sim_means.mean()*100,3),
                "std": round(sim_means.std()*100,3),
                "p10": round(np.percentile(sim_means,10)*100,3),
                "p25": round(np.percentile(sim_means,25)*100,3),
                "p50": round(np.percentile(sim_means,50)*100,3),
                "p75": round(np.percentile(sim_means,75)*100,3),
                "p90": round(np.percentile(sim_means,90)*100,3),
                "p95": round(np.percentile(sim_means,95)*100,3),
            },
            "diff_pct_dist": {
                "mean": round(sim_diffs.mean()*100,3),
                "std": round(sim_diffs.std()*100,3),
                "p10": round(np.percentile(sim_diffs,10)*100,3),
                "p50": round(np.percentile(sim_diffs,50)*100,3),
                "p90": round(np.percentile(sim_diffs,90)*100,3),
                "p95": round(np.percentile(sim_diffs,95)*100,3),
            },
            "ann_pct_dist": {
                "mean": round(sim_anns.mean()*100,2),
                "std": round(sim_anns.std()*100,2),
                "p10": round(np.percentile(sim_anns,10)*100,2),
                "p50": round(np.percentile(sim_anns,50)*100,2),
                "p90": round(np.percentile(sim_anns,90)*100,2),
                "p95": round(np.percentile(sim_anns,95)*100,2),
            },
        },
        "percentiles": {
            "mean_pct": round(pct_mean, 1),
            "diff_pct": round(pct_diff, 1),
            "ann_pct": round(pct_ann, 1),
        },
        "verdict": {
            "mean": verdict_mean,
            "diff": verdict_diff,
            "ann": verdict_ann,
            "overall": overall,
        },
        # 分布直方图数据（用于可视化）
        "hist_means": np.histogram(sim_means*100, bins=40)[0].tolist(),
        "hist_means_edges": np.histogram(sim_means*100, bins=40)[1].tolist(),
        "hist_diffs": np.histogram(sim_diffs*100, bins=40)[0].tolist(),
        "hist_diffs_edges": np.histogram(sim_diffs*100, bins=40)[1].tolist(),
    }
    json.dump(out, open("wuxing_random_result.json", "w"), ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: wuxing_random_result.json  耗时{time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
