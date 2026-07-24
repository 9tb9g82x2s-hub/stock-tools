#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S012 第二轮 · 汇总可视化报告生成器。读取两份结果JSON，产出自包含HTML。"""
import json

BASE = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-18-S012-五行月份LightGBM选股"

w = json.load(open(f"{BASE}/wuxing_weight_result.json"))
r = json.load(open(f"{BASE}/wuxing_random_result.json"))

schemes = w["schemes"]
dates = w["dates"]
base = schemes["基线(=S009原版)"]

# 简化X轴标签：年份
xlabels = [d[:4] + "-" + d[4:6] for d in dates]
xlabels_full = ["起点"] + xlabels

# 挑选几条代表性净值曲线画图
curve_keys = ["基线(=S009原版)", "无杠杆·旺1.0弱0.7", "带杠杆·旺1.3弱0.7"]
curve_colors = ["#888780", "#1D9E75", "#E24B4A"]
curve_datasets = []
for k, c in zip(curve_keys, curve_colors):
    curve_datasets.append({
        "label": k, "data": schemes[k]["nav_curve"], "color": c,
    })

# 对比表行
rows = []
for name, s in schemes.items():
    d_ann = round(s["annual_pct"] - base["annual_pct"], 2)
    d_mdd = round(s["mdd_pct"] - base["mdd_pct"], 1)
    grp = "基线" if name.startswith("基线") else ("无杠杆" if "无杠杆" in name else "带杠杆")
    rows.append({
        "name": name, "grp": grp,
        "total": s["total_pct"], "ann": s["annual_pct"], "mdd": s["mdd_pct"],
        "sharpe": s["sharpe"], "win": s["win_pct"], "d_ann": d_ann, "d_mdd": d_mdd,
    })

# 随机对照直方图
hist_m = r["hist_means"]
hist_m_edges = r["hist_means_edges"]
hist_m_centers = [round((hist_m_edges[i]+hist_m_edges[i+1])/2, 2) for i in range(len(hist_m))]
real_mean = r["real_wuxing"]["wang_mean_pct"]
pct_mean = r["percentiles"]["mean_pct"]
pct_diff = r["percentiles"]["diff_pct"]
pct_ann = r["percentiles"]["ann_pct"]

data_js = {
    "xlabels": xlabels_full,
    "curves": curve_datasets,
    "rows": rows,
    "hist_centers": hist_m_centers,
    "hist_vals": hist_m,
    "real_mean": real_mean,
    "sim_mean": r["simulation"]["mean_pct_dist"]["mean"],
    "sim_p90": r["simulation"]["mean_pct_dist"]["p90"],
    "pct_mean": pct_mean, "pct_diff": pct_diff, "pct_ann": pct_ann,
    "real": r["real_wuxing"],
    "verdict": r["verdict"],
    "meta_r": r["meta"],
}

html = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>S012 五行月份择时 · 第二轮回测报告</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
  background: #f5f5f0; color: #2c2c2a; line-height: 1.6; padding: 32px 20px; }
.wrap { max-width: 980px; margin: 0 auto; }
h1 { font-size: 24px; font-weight: 600; margin-bottom: 6px; }
.sub { color: #5f5e5a; font-size: 14px; margin-bottom: 28px; }
.card { background: #fff; border: 1px solid rgba(0,0,0,.08); border-radius: 14px;
  padding: 22px 24px; margin-bottom: 22px; }
h2 { font-size: 17px; font-weight: 600; margin-bottom: 4px; display: flex; align-items: center; gap: 8px; }
h2 .tag { font-size: 12px; font-weight: 500; padding: 2px 9px; border-radius: 20px; }
.tag-a { background: #E1F5EE; color: #0F6E56; }
.tag-b { background: #FCEBEB; color: #A32D2D; }
.desc { color: #5f5e5a; font-size: 13.5px; margin-bottom: 16px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 8px 10px; text-align: right; border-bottom: 1px solid #eee; }
th:first-child, td:first-child { text-align: left; }
th { color: #5f5e5a; font-weight: 500; font-size: 12px; }
.grp-base td { background: #f7f6f2; font-weight: 500; }
.grp-无杠杆 td:first-child { color: #0F6E56; }
.grp-带杠杆 td:first-child { color: #A32D2D; }
.up { color: #d33; } .down { color: #1a9e60; }
.chart-box { position: relative; width: 100%; height: 320px; margin: 8px 0; }
.metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 16px 0; }
.metric { background: #f7f6f2; border-radius: 10px; padding: 14px 16px; }
.metric .l { font-size: 12px; color: #5f5e5a; }
.metric .v { font-size: 26px; font-weight: 600; margin-top: 2px; }
.verdict-box { border-radius: 10px; padding: 16px 18px; font-size: 14px; margin-top: 14px; }
.v-fail { background: #FCEBEB; color: #791f1f; border: 1px solid #F09595; }
.v-warn { background: #FAEEDA; color: #633806; border: 1px solid #FAC775; }
.legend { display: flex; flex-wrap: wrap; gap: 16px; font-size: 12px; color: #5f5e5a; margin-bottom: 10px; }
.legend span { display: flex; align-items: center; gap: 5px; }
.dot { width: 11px; height: 3px; border-radius: 2px; display: inline-block; }
.concl { font-size: 14px; }
.concl li { margin: 8px 0 8px 18px; }
.big-verdict { font-size: 15px; font-weight: 500; padding: 18px 20px; border-radius: 12px;
  background: #2c2c2a; color: #fff; margin-bottom: 22px; }
.big-verdict b { color: #FAC775; }
.foot { color: #888780; font-size: 12px; text-align: center; margin-top: 24px; }
</style></head><body><div class="wrap">
<h1>S012 · 五行月份择时 第二轮回测报告</h1>
<div class="sub">基于 S009-LightGBM 选股引擎 113 期真实收益 · 温和加权 vs 随机对照 · 2017-01 ~ 2026-07</div>

<div class="big-verdict" id="bigVerdict"></div>

<div class="card">
<h2>实验一 · 五行月份仓位加权回测 <span class="tag tag-a">温和版·不清仓</span></h2>
<div class="desc">首轮已证明"弱月清仓"是负优化。本轮改用"仓位系数加权"——旺月满仓/加仓，弱月减仓但不清仓（剩余持现金年化3%），不打断复利。水月按老大决策统一归入旺月。</div>
<div class="legend" id="curveLegend"></div>
<div class="chart-box"><canvas id="navChart" role="img" aria-label="各方案净值曲线对比"></canvas></div>
<table id="cmpTable"><thead><tr>
<th>方案</th><th>累计收益</th><th>年化</th><th>最大回撤</th><th>夏普</th><th>胜率</th><th>年化Δ</th><th>回撤Δ</th>
</tr></thead><tbody></tbody></table>
<div class="concl" style="margin-top:14px;color:#5f5e5a;font-size:13px;">
Δ 为相对基线(S009原版)的变化。红色=对策略更有利方向（涨/回撤收窄），绿色=不利。
</div>
</div>

<div class="card">
<h2>实验二 · 随机对照实验 <span class="tag tag-b">科学性判决</span></h2>
<div class="desc">蒙特卡洛 N=2000：从113期里随机抽65期当"旺月"，重复2000次得到随机分布，看真实五行旺月收益落在随机分布的哪个分位。>90%分位才算统计显著。</div>
<div class="metrics">
<div class="metric"><div class="l">旺月均月收益 · 分位</div><div class="v" id="mP"></div></div>
<div class="metric"><div class="l">旺-弱收益差 · 分位</div><div class="v" id="dP"></div></div>
<div class="metric"><div class="l">旺月年化 · 分位</div><div class="v" id="aP"></div></div>
</div>
<div class="chart-box"><canvas id="histChart" role="img" aria-label="随机对照收益分布直方图"></canvas></div>
<div class="verdict-box v-fail" id="v2verdict"></div>
</div>

<div class="card">
<h2>综合结论</h2>
<ul class="concl" id="conclList"></ul>
</div>

<div class="foot">S012 第二轮 · 泓锦 · 数据复用 S009 v1.5（无重训） · 生成于回测当日</div>
</div>
<script>
const D = __DATA__;

document.getElementById('bigVerdict').innerHTML =
  '判决：<b>五行月份是"强度信号"而非"择时开关"，且未通过随机对照的显著性检验。</b><br>' +
  '温和加权无法帕累托改善基线（无杠杆减仓一律双输，带杠杆增益仅靠放大风险换来）；' +
  '随机对照三项指标分位均约83%，<b>未达90%显著线</b>——五行与随机择时无统计差异。';

// 净值曲线
const lg = document.getElementById('curveLegend');
D.curves.forEach(c => {
  const s = document.createElement('span');
  s.innerHTML = '<span class="dot" style="background:'+c.color+'"></span>'+c.label;
  lg.appendChild(s);
});
new Chart(document.getElementById('navChart'), {
  type: 'line',
  data: { labels: D.xlabels, datasets: D.curves.map(c => ({
    label: c.label, data: c.data, borderColor: c.color, backgroundColor: c.color,
    borderWidth: 2, pointRadius: 0, tension: 0.1 })) },
  options: { responsive: true, maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: { y: { type: 'logarithmic', title: { display: true, text: '净值(对数轴)' } },
      x: { ticks: { maxTicksLimit: 12, autoSkip: true } } } }
});

// 对比表
const tb = document.querySelector('#cmpTable tbody');
D.rows.forEach(r => {
  const tr = document.createElement('tr');
  tr.className = 'grp-' + (r.grp==='基线'?'base':r.grp);
  const annCls = r.d_ann > 0 ? 'up' : (r.d_ann < 0 ? 'down' : '');
  const mddCls = r.d_mdd > 0 ? 'up' : (r.d_mdd < 0 ? 'down' : '');
  const annTxt = r.name.startsWith('基线') ? '—' : (r.d_ann>0?'+':'')+r.d_ann;
  const mddTxt = r.name.startsWith('基线') ? '—' : (r.d_mdd>0?'+':'')+r.d_mdd;
  tr.innerHTML = '<td>'+r.name+'</td><td>+'+r.total+'%</td><td>'+r.ann+'%</td>'+
    '<td>'+r.mdd+'%</td><td>'+r.sharpe+'</td><td>'+r.win+'%</td>'+
    '<td class="'+annCls+'">'+annTxt+'</td><td class="'+mddCls+'">'+mddTxt+'</td>';
  tb.appendChild(tr);
});

// 分位卡片
function pctColor(p){ return p>=90 ? '#0F6E56' : (p>=70 ? '#BA7517' : '#A32D2D'); }
[['mP',D.pct_mean],['dP',D.pct_diff],['aP',D.pct_ann]].forEach(([id,p])=>{
  const el = document.getElementById(id);
  el.textContent = p + '%';
  el.style.color = pctColor(p);
});

// 直方图 + 真实值标注
new Chart(document.getElementById('histChart'), {
  type: 'bar',
  data: { labels: D.hist_centers, datasets: [{
    label: '随机分布', data: D.hist_vals,
    backgroundColor: D.hist_centers.map(c => c >= D.real_mean ? '#F09595' : '#C0D0E0'),
    borderWidth: 0, barPercentage: 1, categoryPercentage: 1 }] },
  options: { responsive: true, maintainAspectRatio: false,
    plugins: { legend: { display: false },
      title: { display: true, text: '随机旺月均月收益分布（红竖线=真实五行 '+D.real_mean+'%，落在 '+D.pct_mean+'% 分位）' } },
    scales: { x: { title: { display: true, text: '旺月平均月收益 %' }, ticks: { maxTicksLimit: 10 } },
      y: { title: { display: true, text: '模拟次数' } } } }
});

document.getElementById('v2verdict').innerHTML =
  '<b>综合判断：</b>' + D.verdict.overall.replace('✗','✗ ') +
  '<br><br>· 均月收益：' + D.verdict.mean +
  '<br>· 旺弱差：' + D.verdict.diff +
  '<br>· 旺月年化：' + D.verdict.ann;

// 结论
const cl = document.getElementById('conclList');
[
  '<b>五行是强度信号，不是空仓/择时开关。</b>旺月(木火水)均月收益+3.76%确实高于弱月+2.38%，但差距不足以支撑任何减仓动作。',
  '<b>无杠杆减仓一律双输。</b>弱月减仓0.6~0.8的四个方案，年化从41%降到36~38.5%，回撤反而从-20.9%恶化到-22~-24%——因为弱月同样是正收益，减仓只是白白让出复利。',
  '<b>带杠杆的"增益"是假象。</b>旺月加杠杆年化能冲到44.6%，但回撤同步恶化到-33.4%，夏普不升反降(1.457→1.404)，纯粹是放大风险换收益，没有alpha。',
  '<b>随机对照未过关。</b>三项指标分位均约83%，未达90%显著线——真实五行与"随机抽6个月"在统计上无区别，"五行"因果不成立，只能定性为弱季节性效应。',
  '<b>最终建议：S012 维持 S009 v1.5 原版满仓不择时。</b>月份择时（无论叫五行还是季节性）在此选股引擎上不产生可靠增益。S009的alpha来自"持续在场+选股能力"，任何试图择时的改动都是在削弱它。'
].forEach(t => { const li=document.createElement('li'); li.innerHTML=t; cl.appendChild(li); });
</script></body></html>"""

html = html.replace("__DATA__", json.dumps(data_js, ensure_ascii=False))
open(f"{BASE}/S012_第二轮回测报告.html", "w", encoding="utf-8").write(html)
print("报告已生成: S012_第二轮回测报告.html")

# 同时保存汇总JSON
summary = {
    "experiment_1_weight": {name: {k: v for k, v in s.items() if k != "nav_curve"}
                            for name, s in schemes.items()},
    "experiment_2_random": {
        "real": r["real_wuxing"], "percentiles": r["percentiles"], "verdict": r["verdict"],
    },
    "final_conclusion": "五行月份是强度信号非择时开关；温和加权无法帕累托改善基线；随机对照三项分位约83%未达显著线；建议S012维持S009 v1.5原版满仓不择时。",
}
json.dump(summary, open(f"{BASE}/wuxing_v2_result.json", "w"), ensure_ascii=False, indent=2, default=str)
print("汇总已保存: wuxing_v2_result.json")
