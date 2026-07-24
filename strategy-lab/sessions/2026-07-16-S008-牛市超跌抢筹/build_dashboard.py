#!/usr/bin/env python3
"""S008 Dashboard 生成器 —— 每天运行，刷新看板HTML"""
import sqlite3, json, pandas as pd, numpy as np, os
from datetime import datetime, timedelta

DB = os.path.expanduser('~/stock-data/s008_track.db')
OUT = os.path.expanduser('~/stock-tools/strategy-lab/sessions/2026-07-16-S008-牛市超跌抢筹/dashboard.html')

conn = sqlite3.connect(DB)

# 确保表存在
conn.execute('''CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts_code TEXT, name TEXT,
    entry_date TEXT, entry_price REAL, exit_date TEXT, exit_price REAL,
    status TEXT DEFAULT 'holding', holding_days INTEGER DEFAULT 0,
    return_pct REAL, peak_return REAL DEFAULT 0)''')
conn.execute('''CREATE TABLE IF NOT EXISTS nav (
    date TEXT PRIMARY KEY, total_value REAL, cash REAL,
    positions_value REAL, n_positions INTEGER, total_return_pct REAL)''')
conn.execute('''CREATE TABLE IF NOT EXISTS scan_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, scan_date TEXT,
    bull_status TEXT, new_signals INTEGER, brief TEXT)''')

# === 市场状态 ===
import akshare as ak
idx = ak.stock_zh_index_daily(symbol='sh000300')
idx['date'] = pd.to_datetime(idx['date'])
idx['ma200'] = idx['close'].rolling(200).mean()
bull = bool(idx.iloc[-1]['close'] > idx.iloc[-1]['ma200'])
hs300 = float(idx.iloc[-1]['close'])
ma200 = float(idx.iloc[-1]['ma200'])

# === 持仓数据 ===
holdings_df = pd.read_sql("""SELECT ts_code, name, entry_date, entry_price, 
    holding_days, return_pct, peak_return
    FROM positions WHERE status='holding' ORDER BY return_pct DESC""", conn)

# 板块分类
def get_market(code):
    if code.startswith('300') or code.startswith('301'): return '创业板'
    if code.startswith('688'): return '科创板'
    if code.startswith('00'): return '深证'
    return '上证'

holdings_df['market'] = holdings_df['ts_code'].apply(get_market)

# === 已平仓统计 ===
closed = conn.execute("""SELECT COUNT(*), COALESCE(AVG(return_pct),0), 
    COALESCE(AVG(CASE WHEN return_pct>0 THEN 1 ELSE 0 END),0), 
    COALESCE(SUM(return_pct),0)
    FROM positions WHERE status='closed'""").fetchone()

# === 净值历史 ===
nav_df = pd.read_sql("SELECT date, n_positions, total_return_pct FROM nav ORDER BY date", conn)

# === 扫描日志 ===
scan_df = pd.read_sql("SELECT * FROM scan_log ORDER BY scan_date DESC", conn)
last_scan = scan_df.iloc[0].to_dict() if len(scan_df) > 0 else {}

conn.close()

# === 构建JSON ===
data = {
    'update_time': datetime.now().strftime('%Y-%m-%d %H:%M'),
    'bull': bull, 'hs300': round(hs300), 'ma200': round(ma200),
    'n_holding': len(holdings_df),
    'avg_return': round(holdings_df['return_pct'].mean(), 1) if len(holdings_df) > 0 else 0,
    'n_up': int((holdings_df['return_pct'] > 0).sum()) if len(holdings_df) > 0 else 0,
    'n_down': int((holdings_df['return_pct'] < 0).sum()) if len(holdings_df) > 0 else 0,
    'closed_n': int(closed[0]), 'closed_ret': round(closed[1], 1), 'closed_wr': round(closed[2]*100),
    'closed_total': round(closed[3], 1),
    'market_dist': holdings_df['market'].value_counts().to_dict() if len(holdings_df) > 0 else {},
    'holdings': holdings_df.to_dict('records'),
    'nav': nav_df.to_dict('records') if len(nav_df) > 0 else [],
    'last_scan': last_scan,
}

data_json = json.dumps(data, ensure_ascii=False, default=str)

# === 生成HTML ===
html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>S008 牛市超跌抢筹</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
:root{{--bg:#fff;--s:#f8f9fa;--b:#e9ecef;--t:#212529;--t2:#6c757d;--t3:#adb5bd;--r:#e03131;--g:#2f9e44;--blu:#1c7ed6;--rb:#fff5f5;--gb:#f0fff4;--rad:12px}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,"PingFang SC",sans-serif;background:var(--bg);color:var(--t);padding:24px 32px;line-height:1.5}}
h1{{font-size:22px;font-weight:600;margin-bottom:2px}}
.sub{{font-size:13px;color:var(--t3);margin-bottom:20px}}
.g{{display:grid;gap:12px}}
.g4{{grid-template-columns:repeat(4,minmax(0,1fr))}}
.g2{{grid-template-columns:repeat(2,minmax(0,1fr))}}
.c{{background:var(--s);border-radius:var(--rad);padding:16px 20px;border:.5px solid var(--b)}}
.cl{{font-size:12px;color:var(--t2);margin-bottom:2px}}
.cv{{font-size:26px;font-weight:600}}
.cs{{font-size:11px;color:var(--t3);margin-top:2px}}
.r{{color:var(--r)}}.gr{{color:var(--g)}}
.badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:500}}
.bb{{background:#fff3e0;color:#e8590c}}.bbe{{background:#e3f2fd;color:#1565c0}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;padding:8px 12px;border-bottom:1px solid var(--b);color:var(--t2);font-weight:500;font-size:12px}}
td{{padding:7px 12px;border-bottom:.5px solid var(--b)}}
tr:hover{{background:var(--s)}}
.cw{{position:relative;width:100%;height:260px}}
.tag{{display:inline-block;padding:1px 6px;border-radius:3px;font-size:11px}}
.tu{{background:var(--rb);color:var(--r)}}.td{{background:var(--gb);color:var(--g)}}
.ft{{margin-top:32px;padding-top:16px;border-top:.5px solid var(--b);font-size:12px;color:var(--t3)}}
.hold-table{{max-height:500px;overflow-y:auto}}
</style>
</head>
<body>
<h1>S008 牛市超跌抢筹</h1>
<p class="sub">4条件 · 60天持有 · 每日自动刷新 · {data['update_time']}</p>

<div class="g g4" style="margin-bottom:20px">
<div class="c"><div class="cl">市场状态</div><div class="cv" style="font-size:18px"><span class="badge {'bb' if data['bull'] else 'bbe'}">{'🐂 牛市' if data['bull'] else '🐻 熊市'}</span></div><div class="cs">沪深300 {data['hs300']} | MA200 {data['ma200']}</div></div>
<div class="c"><div class="cl">当前持仓</div><div class="cv">{data['n_holding']}<span style="font-size:14px;color:var(--t2)"> 笔</span></div><div class="cs">{'🔴'+str(data['n_up'])+'涨' if data['n_up']>0 else ''} {'🟢'+str(data['n_down'])+'跌' if data['n_down']>0 else ''} 均浮盈{data['avg_return']:+.1f}%</div></div>
<div class="c"><div class="cl">已平仓</div><div class="cv">{data['closed_n']}<span style="font-size:14px;color:var(--t2)"> 笔</span></div><div class="cs">{'均'+str(data['closed_ret'])+'%' if data['closed_n']>0 else '尚无'} {'胜率'+str(data['closed_wr'])+'%' if data['closed_n']>0 else ''}</div></div>
<div class="c"><div class="cl">策略回测</div><div class="cv gr">+42.4%</div><div class="cs">96笔/7年 · 胜率100% · 60日均</div></div>
</div>

<div class="g g2" style="margin-bottom:20px">
<div class="c"><div class="cl" style="margin-bottom:8px">净值曲线</div><div class="cw"><canvas id="navChart"></canvas></div></div>
<div class="c"><div class="cl" style="margin-bottom:8px">板块分布</div><div class="cw"><canvas id="mktChart"></canvas></div></div>
</div>

<div class="c" style="margin-bottom:20px">
<div class="cl" style="margin-bottom:8px">持仓明细（按浮盈排序）</div>
<div class="hold-table"><table id="holdTable"><thead><tr><th>代码</th><th>名称</th><th>入场</th><th>价</th><th>天</th><th>浮盈</th><th>峰值</th><th>板块</th></tr></thead><tbody></tbody></table></div>
</div>

<div class="c"><div class="cl" style="margin-bottom:8px">策略说明</div>
<div style="font-size:13px;color:var(--t2);line-height:1.8">
<strong>入场条件：</strong>下跌 &lt;100天 · 日均跌 &lt;-0.4% · 振幅收敛 &lt;1.2x · OBV底背离<br>
<strong>持有：</strong>60个交易日 · 无止盈止损 · 到期自动卖出<br>
<strong>历史：</strong>2019-2026 共96笔 · 胜率100% · 均收益+42.4% · 中位+34.8%
</div></div>

<div class="ft">S008 策略实验室 · 每日9:30自动扫描 · 看板自动刷新</div>

<script>
var D = {data_json};

// 净值曲线
var navCtx = document.getElementById('navChart').getContext('2d');
if(D.nav.length>0){{
  new Chart(navCtx,{{
    type:'line',data:{{labels:D.nav.map(function(n){{return n.date}}),datasets:[{{label:'已平仓累计收益%',data:D.nav.map(function(n){{return n.total_return_pct}}),borderColor:'#2f9e44',backgroundColor:'rgba(47,158,68,0.1)',fill:true,pointRadius:3,tension:0.3}}]}},
    options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{display:false}}}},scales:{{y:{{ticks:{{callback:function(v){{return v.toFixed(0)+'%'}}}}}}}}}}
  }});
}}else{{
  navCtx.canvas.parentElement.innerHTML='<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--t3)">尚无数据，等待首笔平仓</div>';
}}

// 板块分布
var mktCtx = document.getElementById('mktChart').getContext('2d');
var mkts = D.market_dist;
if(Object.keys(mkts).length>0){{
  new Chart(mktCtx,{{
    type:'doughnut',data:{{labels:Object.keys(mkts),datasets:[{{data:Object.values(mkts),backgroundColor:['#e03131','#1c7ed6','#2f9e44','#f59f00','#7950f2'],borderWidth:0}}]}},
    options:{{responsive:true,maintainAspectRatio:false,plugins:{{legend:{{position:'bottom',labels:{{padding:16,font:{{size:12}}}}}}}}}}
  }});
}}

// 持仓表格
var tbody = document.getElementById('holdTable').querySelector('tbody');
if(D.holdings.length>0){{
  D.holdings.forEach(function(h){{
    var ret=h.return_pct,retC=ret>0?'r':(ret<0?'gr':''),retStr=ret!=null?(ret>0?'+':'')+ret.toFixed(1)+'%':'--';
    var peak=h.peak_return,peakStr=peak!=null?peak.toFixed(1)+'%':'--';
    tbody.innerHTML+='<tr><td>'+h.ts_code+'</td><td>'+h.name+'</td><td>'+h.entry_date+'</td><td>'+h.entry_price.toFixed(2)+'</td><td>'+h.holding_days+'</td><td class="'+retC+'">'+retStr+'</td><td>'+peakStr+'</td><td>'+h.market+'</td></tr>';
  }});
}}else{{
  tbody.innerHTML='<tr><td colspan="8" style="text-align:center;color:var(--t3);padding:40px">暂无持仓，等待入场信号</td></tr>';
}}
</script>
</body>
</html>'''

with open(OUT, 'w') as f:
    f.write(html)

print(f"✅ Dashboard 已刷新: {OUT}")
print(f"   持仓{data['n_holding']}笔 | 已平仓{data['closed_n']}笔 | {data['update_time']}")
