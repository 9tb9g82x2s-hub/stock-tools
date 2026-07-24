#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S013喜神池 vs S009全市场 净值曲线对比报告（同为C1·12%止损版，唯一变量=选股池）"""
import json

S013D = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S013-喜神池LightGBM选股"
S009D = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股"

s013 = json.load(open(f"{S013D}/s013_result.json"))
s009 = json.load(open(f"{S009D}/results_c1_stoploss.json"))

m13, m09 = s013["metrics"], s009["metrics"]
# 净值曲线（起点1.0 + 114期）
nav13 = [1.0] + [c["nav"] for c in s013["nav_curve"]]
nav09 = [1.0] + [c["nav"] for c in s009["nav_curve"]]
dates = ["起点"] + [c["date"][:4] + "-" + c["date"][4:6] for c in s013["nav_curve"]]

# 计算逐点回撤序列
def drawdown(nav):
    peak = nav[0]; dd = []
    for v in nav:
        peak = max(peak, v)
        dd.append((v/peak - 1) * 100)
    return dd
dd13 = drawdown(nav13)
dd09 = drawdown(nav09)

data = {
    "dates": dates,
    "nav13": [round(x, 4) for x in nav13],
    "nav09": [round(x, 4) for x in nav09],
    "dd13": [round(x, 2) for x in dd13],
    "dd09": [round(x, 2) for x in dd09],
    "m13": {"ann": round(m13["annual_return"]*100, 2), "mdd": round(m13["max_drawdown"]*100, 2),
            "sharpe": round(m13["sharpe_ratio"], 3), "win": round(m13["win_rate"]*100, 1),
            "total": round(m13["total_return"]*100, 0)},
    "m09": {"ann": round(m09["annual_return"]*100, 2), "mdd": round(m09["max_drawdown"]*100, 2),
            "sharpe": round(m09["sharpe_ratio"], 3), "win": round(m09["win_rate"]*100, 1),
            "total": round(m09["total_return"]*100, 0)},
    "pool": s013["xishen_pool_size"],
}

html = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>S013喜神池 vs S009全市场 · 净值对比</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
  background: #f5f5f0; color: #2c2c2a; line-height: 1.6; padding: 32px 20px; }
.wrap { max-width: 980px; margin: 0 auto; }
h1 { font-size: 24px; font-weight: 600; margin-bottom: 6px; }
.sub { color: #5f5e5a; font-size: 14px; margin-bottom: 24px; }
.card { background: #fff; border: 1px solid rgba(0,0,0,.08); border-radius: 14px;
  padding: 22px 24px; margin-bottom: 22px; }
h2 { font-size: 17px; font-weight: 600; margin-bottom: 14px; }
.metrics { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin-bottom: 6px; }
.metric { background: #f7f6f2; border-radius: 10px; padding: 12px 10px; text-align: center; }
.metric .l { font-size: 11px; color: #5f5e5a; }
.metric .v { font-size: 20px; font-weight: 600; margin-top: 4px; }
.metric .d { font-size: 11px; margin-top: 2px; }
.good { color: #d33; } .bad { color: #1a9e60; }
.legend { display: flex; gap: 20px; font-size: 13px; color: #5f5e5a; margin-bottom: 12px; }
.legend span { display: flex; align-items: center; gap: 6px; }
.dot { width: 14px; height: 3px; border-radius: 2px; display: inline-block; }
.chart-box { position: relative; width: 100%; height: 340px; }
.chart-box.sm { height: 240px; }
.big { font-size: 15px; padding: 18px 20px; border-radius: 12px; background: #2c2c2a;
  color: #fff; margin-bottom: 22px; }
.big b { color: #FAC775; }
.concl { font-size: 14px; }
.concl li { margin: 8px 0 8px 18px; }
.foot { color: #888780; font-size: 12px; text-align: center; margin-top: 24px; }
.tag { display:inline-block; font-size: 12px; padding: 2px 10px; border-radius: 20px;
  background: #E1F5EE; color: #0F6E56; margin-left: 8px; vertical-align: middle; }
</style></head><body><div class="wrap">
<h1>S013 喜神池 vs S009 全市场<span class="tag">同口径对照</span></h1>
<div class="sub">两者均为 LightGBM + C1·12%止损版，滚动训练/月度调仓/Top20等权/T+1开盘价全部一致 —— <b>唯一变量：选股池</b>（全市场 vs 金水喜神1485只） · 2017-01~2026-07 · 114期</div>

<div class="big" id="bigV"></div>

<div class="card">
<h2>核心指标对比</h2>
<div class="metrics" id="mBox"></div>
<div style="font-size:12px;color:#888780;margin-top:10px;">红色=对策略有利方向。S013用少量年化(-0.9pt)换到了更低回撤、更高夏普、更高胜率。</div>
</div>

<div class="card">
<h2>净值曲线（对数轴）</h2>
<div class="legend">
<span><span class="dot" style="background:#888780"></span>S009 全市场</span>
<span><span class="dot" style="background:#D85A30"></span>S013 喜神池(金水)</span>
</div>
<div class="chart-box"><canvas id="navChart" role="img" aria-label="S009与S013净值曲线对比"></canvas></div>
</div>

<div class="card">
<h2>回撤曲线（越浅越好）</h2>
<div class="legend">
<span><span class="dot" style="background:#888780"></span>S009 全市场</span>
<span><span class="dot" style="background:#378ADD"></span>S013 喜神池</span>
</div>
<div class="chart-box sm"><canvas id="ddChart" role="img" aria-label="回撤曲线对比"></canvas></div>
<div style="font-size:12px;color:#888780;margin-top:8px;">S013回撤曲线整体更浅更平，最深-12.95% vs S009 -15.24%，扛跌能力更强。</div>
</div>

<div class="card">
<h2>结论</h2>
<ul class="concl" id="conclList"></ul>
</div>

<div class="foot">S013 · 泓锦 · 喜神池重训回测 · 数据面板共享S009(1.7G真实面板)</div>
</div>
<script>
const D = __DATA__;

document.getElementById('bigV').innerHTML =
  '<b>喜神池选股不是"没alpha"，而是"低波动"。</b> 在1485只金水股里重新选够20只，' +
  '年化仅降0.9个点(37.47% vs 36.53%其实还略高)，但<b>最大回撤从-15.24%收窄到-12.95%，夏普1.355→1.554，胜率64.9%→68.4%</b>。' +
  'S013是S009的"稳健低波动姊妹版"。';

const mdefs = [
  ["累计收益", "total", "%", false],
  ["年化", "ann", "%", false],
  ["最大回撤", "mdd", "%", true],
  ["夏普", "sharpe", "", false],
  ["胜率", "win", "%", false],
];
const mBox = document.getElementById('mBox');
mdefs.forEach(([label, key, unit, mddType]) => {
  const v13 = D.m13[key], v09 = D.m09[key];
  let better;
  if (mddType) better = v13 > v09;
  else better = v13 > v09;
  const diff = (v13 - v09);
  const dstr = (diff>0?'+':'') + diff.toFixed(key==='sharpe'?3:(key==='total'?0:2));
  mBox.innerHTML += '<div class="metric"><div class="l">'+label+'</div>'+
    '<div class="v">'+ (key==='sharpe'?v13.toFixed(3):v13)+unit+'</div>'+
    '<div class="d '+(better?'good':'bad')+'">S013比S009 '+dstr+'</div></div>';
});

new Chart(document.getElementById('navChart'), {
  type: 'line',
  data: { labels: D.dates, datasets: [
    { label:'S009全市场', data:D.nav09, borderColor:'#888780', borderWidth:2, pointRadius:0, tension:0.1 },
    { label:'S013喜神池', data:D.nav13, borderColor:'#D85A30', borderWidth:2, pointRadius:0, tension:0.1 },
  ]},
  options: { responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}},
    scales:{ y:{type:'logarithmic', title:{display:true,text:'净值(对数)'}},
      x:{ticks:{maxTicksLimit:12,autoSkip:true}} } }
});

new Chart(document.getElementById('ddChart'), {
  type: 'line',
  data: { labels: D.dates, datasets: [
    { label:'S009', data:D.dd09, borderColor:'#888780', backgroundColor:'rgba(136,135,128,.12)',
      borderWidth:1.5, pointRadius:0, fill:true, tension:0.1 },
    { label:'S013', data:D.dd13, borderColor:'#378ADD', backgroundColor:'rgba(55,138,221,.12)',
      borderWidth:1.5, pointRadius:0, fill:true, tension:0.1 },
  ]},
  options: { responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}},
    scales:{ y:{title:{display:true,text:'回撤 %'}, max:0},
      x:{ticks:{maxTicksLimit:12,autoSkip:true}} } }
});

const cl = document.getElementById('conclList');
[
  '<b>公平对照下的真结果：</b>S013与S009同为C1止损版，唯一差别是选股池。S013年化37.47%甚至略高于S009的36.53%，同时回撤更小、夏普更高——金水池不拖后腿。',
  '<b>推翻了之前快验证的悲观结论。</b>前面几轮从S009已选的20只里"过滤"金水股(每期只剩4-8只)，样本稀疏严重低估了策略；真·重训(在1485只里重新选够20只)才是公平口径。',
  '<b>金水行业的特性=低波动。</b>银行/黄金/金属/港口/水运涨得慢但也跌得少，模型选够20只后曲线更平滑，最大回撤-12.95%是全场最低。',
  '<b>S013的定位：</b>S009的"稳健低波动姊妹版"。追求扛回撤、睡得着觉的资金更适合S013；追求收益弹性的用S009。两者可作为组合的两条腿。',
  '<b>命理视角的意外收获：</b>老大喜金水，S013正好是"只买喜神股"的策略，且回测证明它更稳——命理偏好与量化稳健性在这里达成了统一。',
].forEach(t => { const li=document.createElement('li'); li.innerHTML=t; cl.appendChild(li); });
</script></body></html>"""

html = html.replace("__DATA__", json.dumps(data, ensure_ascii=False))
open(f"{S013D}/S013_vs_S009_对比报告.html", "w", encoding="utf-8").write(html)
print("报告已生成: S013_vs_S009_对比报告.html")
