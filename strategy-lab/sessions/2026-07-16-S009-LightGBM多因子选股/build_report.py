#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S009 report.html 生成器 —— 读取 results.json 生成可视化报告"""
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_PATH = f"{BASE_DIR}/results.json"
OUT_PATH = f"{BASE_DIR}/report.html"

with open(RESULTS_PATH, "r", encoding="utf-8") as f:
    data = json.load(f)

m = data["metrics"]
aux = data["aux_metrics"]
nav_curve = data.get("nav_curve", [])
stocks = data.get("stocks", [])
ai = data.get("ai_analysis", {})

data_json = json.dumps(
    {
        "nav": nav_curve,
        "trades": data.get("trades_summary", []),
    },
    ensure_ascii=False,
)

def pct(x):
    return f"{x*100:+.1f}%"

def pct0(x):
    return f"{x*100:.0f}%"

html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>S009 LightGBM多因子选股</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
:root{{--bg:#fff;--s:#f8f9fa;--b:#e9ecef;--t:#212529;--t2:#6c757d;--t3:#adb5bd;--r:#e03131;--g:#2f9e44;--blu:#1c7ed6;--rad:12px}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,"PingFang SC",sans-serif;background:var(--bg);color:var(--t);padding:24px 32px;line-height:1.5}}
h1{{font-size:22px;font-weight:600;margin-bottom:2px}}
.sub{{font-size:13px;color:var(--t3);margin-bottom:20px}}
.g{{display:grid;gap:12px}}
.g5{{grid-template-columns:repeat(5,minmax(0,1fr))}}
.g2{{grid-template-columns:repeat(2,minmax(0,1fr))}}
.c{{background:var(--s);border-radius:var(--rad);padding:16px 20px;border:.5px solid var(--b)}}
.cl{{font-size:12px;color:var(--t2);margin-bottom:2px}}
.cv{{font-size:26px;font-weight:600}}
.cs{{font-size:11px;color:var(--t3);margin-top:2px}}
.r{{color:var(--r)}}.gr{{color:var(--g)}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;padding:8px 12px;border-bottom:1px solid var(--b);color:var(--t2);font-weight:500;font-size:12px}}
td{{padding:7px 12px;border-bottom:.5px solid var(--b)}}
tr:hover{{background:var(--s)}}
.cw{{position:relative;width:100%;height:280px}}
.ft{{margin-top:32px;padding-top:16px;border-top:.5px solid var(--b);font-size:12px;color:var(--t3)}}
.tag{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;background:#e3f2fd;color:#1565c0;margin-right:6px}}
</style>
</head>
<body>
<h1>S009 LightGBM多因子选股</h1>
<p class="sub">32特征 · 月度滚动训练 · Top20等权 · 回测区间 {aux.get('first_rebalance','-')} ~ {aux.get('last_rebalance','-')}</p>

<div class="g g5" style="margin-bottom:20px">
<div class="c"><div class="cl">总收益</div><div class="cv {'r' if m['total_return']>=0 else 'gr'}">{pct(m['total_return'])}</div><div class="cs">净值 {1+m['total_return']:.2f}</div></div>
<div class="c"><div class="cl">年化收益</div><div class="cv {'r' if m['annual_return']>=0 else 'gr'}">{pct(m['annual_return'])}</div><div class="cs">{aux.get('n_features')}特征 · {aux.get('train_window_months')}月训练窗口</div></div>
<div class="c"><div class="cl">胜率</div><div class="cv">{pct0(m['win_rate'])}</div><div class="cs">{m['total_trades']}期调仓</div></div>
<div class="c"><div class="cl">最大回撤</div><div class="cv gr">{pct(m['max_drawdown'])}</div><div class="cs">基于月度净值</div></div>
<div class="c"><div class="cl">夏普比率</div><div class="cv">{m['sharpe_ratio']:.2f}</div><div class="cs">月度收益年化</div></div>
</div>

<div class="c" style="margin-bottom:20px">
<div class="cl" style="margin-bottom:8px">净值曲线</div>
<div class="cw"><canvas id="navChart"></canvas></div>
</div>

<div class="c" style="margin-bottom:20px">
<div class="cl" style="margin-bottom:8px">最新持仓（{aux.get('last_rebalance','-')}调仓，Top{aux.get('top_n',20)}）</div>
<div>{"".join(f'<span class="tag">{s["code"]}</span>' for s in stocks) if stocks else '<span style="color:var(--t3);font-size:13px">暂无</span>'}</div>
</div>

<div class="c" style="margin-bottom:20px">
<div class="cl" style="margin-bottom:8px">最近调仓记录</div>
<table id="tradeTable"><thead><tr><th>调仓日</th><th>下期日</th><th>持仓数</th><th>本期收益</th><th>盈利数</th></tr></thead><tbody></tbody></table>
</div>

<div class="c"><div class="cl" style="margin-bottom:8px">AI分析</div>
<div style="font-size:13px;color:var(--t2);line-height:1.8">{ai.get('summary','')}</div>
</div>

<div class="ft">S009 策略实验室 · LightGBM(gbdt) · 训练窗口{aux.get('train_window_months')}月 · 生成于本地</div>

<script>
var D = {data_json};

var navCtx = document.getElementById('navChart').getContext('2d');
if(D.nav.length>0){{
  new Chart(navCtx,{{
    type:'line',
    data:{{labels:D.nav.map(function(n){{return n.date}}),datasets:[{{label:'净值',data:D.nav.map(function(n){{return n.nav}}),borderColor:'#1c7ed6',backgroundColor:'rgba(28,126,214,0.08)',fill:true,pointRadius:0,tension:0.15,borderWidth:1.5}}]}},
    options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},scales:{{x:{{ticks:{{maxTicksLimit:12}}}}}}}}
  }});
}}else{{
  navCtx.canvas.parentElement.innerHTML='<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--t3)">暂无净值数据</div>';
}}

var tbody = document.getElementById('tradeTable').querySelector('tbody');
if(D.trades.length>0){{
  D.trades.slice().reverse().forEach(function(t){{
    var ret=t.period_return,retC=ret>0?'r':(ret<0?'gr':''),retStr=(ret>0?'+':'')+(ret*100).toFixed(1)+'%';
    tbody.innerHTML+='<tr><td>'+t.rebalance_date+'</td><td>'+t.next_date+'</td><td>'+t.n_holdings+'</td><td class="'+retC+'">'+retStr+'</td><td>'+t.win_count+'/'+t.n_holdings+'</td></tr>';
  }});
}}else{{
  tbody.innerHTML='<tr><td colspan="5" style="text-align:center;color:var(--t3);padding:40px">暂无调仓记录</td></tr>';
}}
</script>
</body>
</html>'''

with open(OUT_PATH, "w", encoding="utf-8") as f:
    f.write(html)

print(f"report.html 已生成: {OUT_PATH}")
