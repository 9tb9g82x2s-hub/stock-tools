#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
熊市归因：2018 & 2022 两个大熊市，S009 到底赚的是什么钱？
逐期拆解：持仓股的行业分布 + 每只当期实际涨幅，看是不是押中了某个逆势小板块。
"""
import sqlite3, ast, time
import pandas as pd, numpy as np

DB = '/Users/ziruzhu/stock-data/stock_all.db'
BASE = '/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股'

def log(m): print("[%s] %s" % (time.strftime('%H:%M:%S'), m), flush=True)

con = sqlite3.connect(DB)
sinfo = pd.read_sql("SELECT ts_code,name,industry FROM stock_list", con).set_index("ts_code")
t = pd.read_csv(BASE + '/trades_full.csv')
t['year'] = t['rebalance_date'].astype(str).str[:4]

def period_ret(codes, buy_date, sell_date):
    """每只股票 buy开盘->sell开盘 涨幅（前复权 open_qfq 优先，用 stk_factor）"""
    cs = ",".join(["'%s'" % c for c in codes])
    q = "SELECT ts_code,trade_date,open_qfq FROM stk_factor WHERE ts_code IN (%s) AND trade_date IN ('%s','%s')" % (cs, buy_date, sell_date)
    df = pd.read_sql(q, con)
    df['open_qfq'] = pd.to_numeric(df['open_qfq'], errors='coerce')
    op = df[df['trade_date']==buy_date].set_index('ts_code')['open_qfq']
    cl = df[df['trade_date']==sell_date].set_index('ts_code')['open_qfq']
    out = {}
    for c in codes:
        if c in op.index and c in cl.index and op[c]>0:
            out[c] = (cl[c]/op[c]-1)*100
    return out

for yr in ['2018','2022']:
    g = t[t['year']==yr]
    print("\n" + "="*66)
    print("【%s年熊市归因】年度合计 %+.2f%%" % (yr, (np.prod(1+g['period_return'].values)-1)*100))
    print("="*66)
    # 全年行业统计
    ind_counter = {}
    ind_ret = {}   # 行业 -> [该行业个股当期涨幅]
    all_stock_rets = []
    for _,r in g.iterrows():
        codes = ast.literal_eval(r['holdings'])
        rets = period_ret(codes, str(r['buy_date']), str(r['sell_date']))
        for c in codes:
            ind = sinfo.loc[c,'industry'] if c in sinfo.index else '未知'
            ind_counter[ind] = ind_counter.get(ind,0)+1
            if c in rets:
                ind_ret.setdefault(ind,[]).append(rets[c])
                all_stock_rets.append({'code':c,'name':sinfo.loc[c,'name'] if c in sinfo.index else '',
                                       'ind':ind,'ret':rets[c],'period':str(r['rebalance_date'])})
    # 行业出现次数 TOP
    top_ind = sorted(ind_counter.items(), key=lambda x:-x[1])[:12]
    print("\n持仓行业分布(全年累计出现次数 TOP12, 满仓=12期×20=240个仓位):")
    print("  %-12s%8s%12s" % ("行业","出现次数","该行业均涨幅"))
    for ind,cnt in top_ind:
        avg = np.mean(ind_ret.get(ind,[0])) if ind in ind_ret else 0
        print("  %-12s%8d%11.1f%%" % (ind, cnt, avg))
    # 最赚钱的10只持仓
    sr = pd.DataFrame(all_stock_rets).sort_values('ret',ascending=False)
    print("\n全年最赚钱的12笔持仓:")
    print("  %-9s%-10s%8s  %s" % ("股票","行业","涨幅","调仓期"))
    for _,x in sr.head(12).iterrows():
        print("  %-9s%-10s%+7.1f%%  %s" % (x['name'],x['ind'],x['ret'],x['period']))
    # 行业集中度
    total_pos = sum(ind_counter.values())
    top3_share = sum(c for _,c in top_ind[:3])/total_pos*100
    print("\n  行业集中度: 前3大行业占 %.1f%% 的仓位 (总仓位%d)" % (top3_share, total_pos))

con.close()
log("done")
