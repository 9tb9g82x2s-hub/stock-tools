#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 S009-c1 12张月度对比表 HTML（策略 vs 沪深300 vs 上证指数）"""
import pandas as pd, akshare as ak, numpy as np, json

BASE = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股"

# ── 1. 读取c1止损版交易记录 ──
trades = pd.read_csv(f"{BASE}/trades_full_c1_stoploss.csv")
trades['ym'] = pd.to_datetime(trades['sell_date'],format='%Y%m%d').dt.to_period('M')
strategy = trades.set_index('ym')[['period_return']].rename(columns={'period_return':'strategy'})
stops = trades.set_index('ym')['stop_count'] if 'stop_count' in trades.columns else None

# ── 2. 沪深300 + 上证 月收益 ──
hs300 = ak.stock_zh_index_daily(symbol='sh000300')
hs300['date'] = pd.to_datetime(hs300['date'])
hs300['ym'] = hs300['date'].dt.to_period('M')
hs_m = hs300.sort_values('date').groupby('ym')['close'].last().pct_change().dropna()

sz = ak.stock_zh_index_daily(symbol='sh000001')
sz['date'] = pd.to_datetime(sz['date'])
sz['ym'] = sz['date'].dt.to_period('M')
sz_m = sz.sort_values('date').groupby('ym')['close'].last().pct_change().dropna()

# ── 3. 合并 ──
df = pd.DataFrame({'hs300':hs_m,'shanghai':sz_m})
df['strategy'] = strategy['strategy']
if stops is not None:
    df['stops'] = stops
df = df[df.index>='2017-01'].dropna(subset=['strategy'])

df['strategy_cum'] = (1+df['strategy']).cumprod()
df['hs300_cum'] = (1+df['hs300']).cumprod()
df['shanghai_cum'] = (1+df['shanghai']).cumprod()

# ── 4. 保存CSV ──
df_out = df[['strategy','hs300','shanghai','strategy_cum','hs300_cum','shanghai_cum']].copy()
for c in ['strategy','hs300','shanghai']:
    df_out[c] = (df_out[c]*100).round(2)
for c in ['strategy_cum','hs300_cum','shanghai_cum']:
    df_out[c] = df_out[c].round(4)
df_out.to_csv(f"{BASE}/monthly_comparison_c1_stoploss.csv",encoding='utf-8-sig')

# ── 5. 计算全期指标 ──
s_ann = (df['strategy_cum'].iloc[-1])**(1/(len(df)/12))-1
h_ann = (df['hs300_cum'].iloc[-1])**(1/(len(df)/12))-1
z_ann = (df['shanghai_cum'].iloc[-1])**(1/(len(df)/12))-1
dd_strategy = (df['strategy_cum']/df['strategy_cum'].cummax()-1).min()
stop_rate = df['stops'].sum()/(len(df)*20) if stops is not None else np.nan

months_order = ['01','02','03','04','05','06','07','08','09','10','11','12']
month_names = ['1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月']

def fmt(x):
    return f"{x*100:+.2f}%"

# ── 6. 生成HTML ──
rows_html = ""
for mon, mname in zip(months_order, month_names):
    sub = df[df.index.astype(str).str[5:7]==mon]
    if len(sub)==0: continue
    s_mean = sub['strategy'].mean()
    h_mean = sub['hs300'].mean()
    z_mean = sub['shanghai'].mean()
    s_wr = (sub['strategy']>0).mean()*100
    h_wr = (sub['hs300']>0).mean()*100
    z_wr = (sub['shanghai']>0).mean()*100
    s_stops = f"{int(sub['stops'].sum())}/{(len(sub)*20)}" if stops is not None else "—"

    tbody = ""
    for _, row in sub.iterrows():
        yr = str(row.name)[:4]
        tbody += f"<tr><td>{yr}</td><td class=\"num\">{fmt(row['strategy'])}</td><td class=\"num\">{fmt(row['hs300'])}</td><td class=\"num\">{fmt(row['shanghai'])}</td><td class=\"num\">{int(row['stops']) if stops is not None else ''}</td></tr>\n"

    color = "var(--color-text-danger)" if s_mean>0.03 else ("var(--color-text-tertiary)" if s_mean<0.01 else "var(--color-text-primary)")

    rows_html += f"""<div class="month-block" id="m{mon}">
<h3 class="month-title">{mname} <span style="font-weight:400;font-size:13px">月均策略{fmt(s_mean)}(胜率{s_wr:.0f}%) | 指数{fmt(h_mean)}/{fmt(z_mean)} | 止损{s_stops}次</span></h3>
<table><thead><tr><th>年</th><th>S009-c1</th><th>沪深300</th><th>上证指数</th><th>止损(只)</th></tr></thead><tbody>
{tbody}
</tbody></table></div>"""

html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>S009-c1 -12%止损版 月度对比表</title>
<style>
:root{{--bg:#fff;--s:#f8f9fa;--b:#e9ecef;--t:#212529;--t2:#6c757d;--t3:#adb5bd;--r:#e03131;--g:#2f9e44;--rad:12px}}
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,"PingFang SC",sans-serif;background:var(--bg);color:var(--t);padding:24px 32px;line-height:1.5}}
h1{{font-size:20px;font-weight:600;margin-bottom:4px}}
.sub{{font-size:13px;color:var(--t3);margin-bottom:24px}}
.summary{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:24px}}
.sc{{background:var(--s);border-radius:var(--rad);padding:16px 20px;text-align:center}}
.scl{{font-size:12px;color:var(--t2);margin-bottom:4px}}.scv{{font-size:22px;font-weight:600}}.scs{{font-size:11px;color:var(--t3);margin-top:4px}}
.overview{{font-size:13px;color:var(--t2);margin-bottom:24px;line-height:1.8;padding:16px 20px;background:var(--s);border-radius:var(--rad)}}
.month-block{{margin-bottom:24px;border:0.5px solid var(--b);border-radius:var(--rad);overflow:hidden;background:var(--bg)}}
.month-title{{font-size:15px;font-weight:600;padding:12px 20px;background:var(--s);border-bottom:0.5px solid var(--b)}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;padding:8px 12px;border-bottom:1px solid var(--b);color:var(--t2);font-weight:500;font-size:12px;background:var(--s)}}
td{{padding:6px 12px;border-bottom:0.5px solid var(--b)}}
td:first-child{{text-align:center;width:50px}}td.num{{text-align:right;width:100px;font-variant-numeric:tabular-nums}}
tr:hover{{background:var(--s)}}
.nav{{position:sticky;top:0;background:var(--bg);padding:8px 0;margin-bottom:16px;display:flex;gap:6px;flex-wrap:wrap;border-bottom:0.5px solid var(--b);z-index:10}}
.nav a{{font-size:12px;color:var(--blu);text-decoration:none;padding:4px 10px;border-radius:4px;background:var(--s)}}
.nav a:hover{{background:#e3f2fd}}
</style></head><body>
<h1>S009-c1 -12%止损版 · 月度收益对比</h1>
<p class="sub">策略(S009-c1) vs 沪深300 vs 上证指数 | 2017-01 至 2026-06 · T+1开盘价执行 · 含手续费印花税 · 单股-12%止损</p>

<div class="summary">
<div class="sc"><div class="scl">策略年化</div><div class="scv" style="color:var(--r)">{fmt(s_ann)}</div><div class="scs">全期总收益 {fmt(df['strategy_cum'].iloc[-1]-1)}</div></div>
<div class="sc"><div class="scl">HS300年化</div><div class="scv">{fmt(h_ann)}</div><div class="scs">总收益 {fmt(df['hs300_cum'].iloc[-1]-1)}</div></div>
<div class="sc"><div class="scl">上证年化</div><div class="scv">{fmt(z_ann)}</div><div class="scs">总收益 {fmt(df['shanghai_cum'].iloc[-1]-1)}</div></div>
<div class="sc"><div class="scl">止损触发率</div><div class="scv">{stop_rate*100:.1f}%</div><div class="scs">{int(df['stops'].sum()) if stops is not None else 0}只次 / {len(df)*20}只次</div></div>
</div>

<div class="overview">
<strong>版本说明：</strong>在S009 v1.3基础上加入<strong>单股-12%止损</strong>——持仓期间若某股前复权收盘价跌破买入价×0.88，则以该日收盘价止损退出，资金不再重新配置。若未触发止损则正常持有至下期调仓日开盘卖出。<br>
<strong>最大回撤：</strong>策略 {fmt(dd_strategy)} 。止损触发后立即斩仓，不能完全避免回撤但能防"扛单越亏越多"。
</div>

<div class="nav"><strong>跳转：</strong>
{"".join(f'<a href="#m{mon}">{mname}</a>' for mon, mname in zip(months_order, month_names))}
</div>

{rows_html}
</body></html>"""

out = f"{BASE}/c1_stoploss_monthly_tables.html"
with open(out,'w',encoding='utf-8') as f:
    f.write(html)
print(f"HTML已生成: {out}")
