#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成S023持有周期对比HTML报告（红涨绿跌）"""
import json

DIR = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-26-S023-周度5天换仓"
data = json.load(open(f"{DIR}/_report_data.json"))

def sl_tag(sl):
    if sl is None or sl=='None': return '不设'
    return f"{float(sl)*100:.0f}%"

# 各版本最优单策略
best = {}
for hd in ['1','2','3','5']:
    ss = data[hd]['singles']
    b = max(ss, key=lambda x: x['sh'])
    bc = max(data[hd]['combined'], key=lambda x: x['sharpe']) if data[hd]['combined'] else None
    best[hd] = (b, bc)

# 颜色：涨红跌绿（中国惯例）。年化正=红，回撤=绿深浅
def ann_color(v):
    return '#c0392b' if v>0 else '#27ae60'
def dd_color(v):
    # 回撤越深越绿
    a = abs(v)
    if a>0.4: return '#0e6b2e'
    if a>0.25: return '#27ae60'
    if a>0.15: return '#5cb85c'
    return '#8fd19e'

html = []
html.append('''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>S023 持有周期网格对比</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#f5f6fa;color:#2d3436;padding:24px;line-height:1.6}
.wrap{max-width:1160px;margin:0 auto}
h1{font-size:26px;color:#2d3436;margin-bottom:4px}
.sub{color:#909497;font-size:13px;margin-bottom:20px}
.card{background:#fff;border-radius:10px;padding:24px;margin-bottom:18px;box-shadow:0 2px 12px rgba(0,0,0,.06)}
h2{font-size:18px;margin-bottom:14px;color:#2d3436;border-left:4px solid #c0392b;padding-left:10px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:9px 10px;text-align:center;border-bottom:1px solid #ecf0f1}
th{background:#f8f9fa;font-weight:600;color:#576574}
.hl{background:#fff8e1;font-weight:700}
.tag{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}
.win{background:#fde8e8;color:#c0392b}.lose{background:#e8f5e9;color:#27ae60}
.note{background:#fff8e1;border-left:4px solid #f39c12;padding:12px 14px;border-radius:4px;font-size:13px;margin:12px 0}
.concl{background:#f8f9fa;border-radius:8px;padding:16px;line-height:1.9;font-size:14px}
.big{font-size:15px;font-weight:700;color:#c0392b}
</style></head><body><div class="wrap">''')

html.append('<h1>📊 S023 持有周期网格对比报告</h1>')
html.append('<div class="sub">1/2/3/5天持有 × 5相位错开 × 6档止损 | 回测2017-01~2026-07-17（已剔前复权污染周）| 红涨绿跌</div>')

# ===== 核心结论表 =====
html.append('<div class="card"><h2>🏆 核心结论：各持有周期最优单策略</h2>')
html.append('<table><tr><th>持有周期</th><th>最优相位</th><th>止损</th><th>年化收益</th><th>最大回撤</th><th>夏普</th><th>胜率</th><th>vs S019</th></tr>')
for hd in ['1','2','3','5']:
    b,_ = best[hd]
    win = '优于' if b['sh']>2.17 else '劣于'
    tag = 'win' if b['sh']>2.17 else 'lose'
    cls = ' class="hl"' if hd=='5' else ''
    html.append(f'<tr{cls}><td><b>{hd}天</b></td><td>Phase{b["phase"]}</td><td>{sl_tag(b["sl"])}</td>'
                f'<td style="color:{ann_color(b["ann"])};font-weight:700">{b["ann"]*100:.1f}%</td>'
                f'<td style="color:{dd_color(b["dd"])};font-weight:700">{b["dd"]*100:.1f}%</td>'
                f'<td><b>{b["sh"]:.2f}</b></td><td>{b["wr"]*100:.1f}%</td>'
                f'<td><span class="tag {tag}">{win}</span></td></tr>')
html.append('<tr style="background:#eef2f7"><td><b>S019</b>(10日)</td><td>—</td><td>-12%</td>'
            '<td style="color:#c0392b">64.5%</td><td style="color:#8fd19e">-12.2%</td><td>2.17</td><td>70.0%</td><td>基准</td></tr>')
html.append('</table>')
html.append('<div class="note">⚠️ <b>关键规律：持有周期越短，回撤越惨。</b>1天版回撤-52%实际不可用；'
            '持有越长回撤越受控（5天版-14%）。这与"短周期更灵活"的直觉相反——短周期在震荡市每次调仓都可能踩雷，'
            '且交易成本随换手激增（1天版年成本约50%）。</div></div>')

# ===== 分批错开对比 =====
html.append('<div class="card"><h2>🔀 分批错开（多相位组合）vs 单相位</h2>')
html.append('<table><tr><th>持有周期</th><th>组合最优止损</th><th>组合年化</th><th>组合回撤</th><th>组合夏普</th>'
            '<th>单相位最优回撤</th><th>降回撤效果</th></tr>')
for hd in ['1','2','3','5']:
    b,bc = best[hd]
    if not bc or data[hd]['n_phase']<2:
        html.append(f'<tr><td>{hd}天</td><td colspan="6" style="color:#999">仅1相位，无组合</td></tr>')
        continue
    improve = bc['max_drawdown']-b['dd']  # 组合回撤 - 单相位回撤（负=组合回撤更小更好）
    eff = f'{"↓降低" if bc["max_drawdown"]>b["dd"] else "↑扩大"} {abs(improve)*100:.1f}pp'
    ec = 'lose' if bc['max_drawdown']>b['dd'] else 'win'
    cls = ' class="hl"' if hd=='5' else ''
    html.append(f'<tr{cls}><td><b>{hd}天</b></td><td>{sl_tag(bc["stop_loss"])}</td>'
                f'<td style="color:{ann_color(bc["annual_return"])};font-weight:700">{bc["annual_return"]*100:.1f}%</td>'
                f'<td style="color:{dd_color(bc["max_drawdown"])};font-weight:700">{bc["max_drawdown"]*100:.1f}%</td>'
                f'<td>{bc["sharpe"]:.2f}</td><td style="color:{dd_color(b["dd"])}">{b["dd"]*100:.1f}%</td>'
                f'<td><span class="tag {ec}">{eff}</span></td></tr>')
html.append('</table>')
html.append('<div class="note">分批错开（N相位各投1/N资金、起点错开）能<b>显著降低回撤</b>（5天版从-14%降到-11.8%，'
            '2天版从-37%降到-28.5%），但<b>年化会打折</b>（5天版91%→80%）。适合追求低波动的风格；'
            '追最大收益仍是单相位最优。</div></div>')

# ===== 5天版完整止损网格 =====
html.append('<div class="card"><h2>🔬 5天版完整网格（5相位 × 6止损，夏普矩阵）</h2>')
grid = {}
for r in data['5']['singles']:
    grid[(r['phase'], sl_tag(r['sl']))] = r
sls = ['-6%','-8%','-10%','-12%','-15%','不设']
html.append('<table><tr><th>相位＼止损</th>'+''.join(f'<th>{s}</th>' for s in sls)+'</tr>')
for ph in range(5):
    cells=[]
    for s in sls:
        r = grid.get((ph,s))
        if r:
            hl = ' style="background:#fff3cd;font-weight:700"' if (ph==4 and s=='-15%') else ''
            cells.append(f'<td{hl}>{r["sh"]:.2f}<br><span style="font-size:10px;color:#999">{r["ann"]*100:.0f}%/{r["dd"]*100:.0f}%</span></td>')
        else:
            cells.append('<td>—</td>')
    html.append(f'<tr><td><b>Phase{ph}</b></td>'+''.join(cells)+'</tr>')
html.append('</table><div class="note">每格：夏普（大）+ 年化%/回撤%（小）。'
            '<b>Phase4全线最优，且止损线-6%~不设几乎无差异</b>——说明5天持有期内止损不是关键变量，选对相位才是。</div></div>')

# ===== 最终建议 =====
b5,bc5 = best['5']
html.append('<div class="card"><h2>✅ 最终建议</h2><div class="concl">')
html.append(f'<p class="big">推荐定版：5天持有 + Phase4 + 止损-8%</p>')
html.append(f'<p>1. <b>综合最优是5天版Phase4</b>：年化91.4%、回撤-14.0%、夏普2.79、胜率68.4%，'
            f'除回撤略宽于S019(-12.2%)外，其余全面碾压S019(年化64.5%/夏普2.17)。</p>')
html.append(f'<p>2. <b>止损线不敏感</b>：Phase4从-6%到不设，年化/夏普几乎不变。设-8%是"有比没有好"的稳健选择，不必纠结。</p>')
html.append(f'<p>3. <b>不要盲目缩短周期</b>：1天版回撤-52%、2天-37%，短周期看似灵活实则被震荡+成本双杀。3天版年化最高(93.2%)但回撤-22%偏大。</p>')
html.append(f'<p>4. <b>若求低波动</b>：可选5天版5相位分批错开，回撤-11.8%（比S019还小），代价是年化降到79.7%。</p>')
html.append(f'<p style="color:#c0392b;margin-top:8px">⚠️ 下一步建议：Phase4是样本内最优，需做<b>样本外验证</b>（如2024年后滚动前推）确认不是过拟合，再上实盘。</p>')
html.append('</div></div>')

html.append('<div style="text-align:center;color:#b2bec3;font-size:12px;padding:16px">'
            'S023持有周期网格 | Studio并行回测 | 2026-07-28 07:10 | 数据已剔除前复权污染周，4版本单期极端值核查通过</div>')
html.append('</div></body></html>')

open(f"{DIR}/s023_holdperiod_report.html","w").write('\n'.join(html))
print("报告已生成: s023_holdperiod_report.html")
