#!/usr/bin/env python3
"""按交易日期拉当天全市场日线/basic，写入stock_all.db"""
import requests, sqlite3, time

TOKEN = '2b6b1b830a45468b9856e6500ce40a90'
BASE  = 'https://ts.gyzcloud.top/api'
DB    = '/Users/ziruzhu/stock-data/stock_all.db'
DATE  = '20260721'

def call(api, params, fields, limit=8000):
    r = requests.post(BASE, json={
        'api_name': api, 'token': TOKEN,
        'params': params, 'fields': fields
    }, timeout=30)
    d = r.json()
    if d.get('code') != 0:
        raise Exception(d.get('msg', '未知错误'))
    return d['data']['fields'], d['data']['items']

conn = sqlite3.connect(DB)
conn.execute('PRAGMA journal_mode=WAL')

# ── 1. daily 日线 ──────────────────────────────────────────────────
print(f"=== 更新 daily ({DATE}) ===")
flds, items = call('daily', {'trade_date': DATE},
    'ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount')
n = 0
for r in items:
    try:
        conn.execute("""INSERT OR REPLACE INTO daily
            (ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            [r[0],r[1],
             float(r[2] or 0), float(r[3] or 0), float(r[4] or 0), float(r[5] or 0),
             float(r[6] or 0) if r[6] else None,
             float(r[7] or 0) if r[7] else None,
             float(r[8] or 0) if r[8] else None,
             float(r[9] or 0), float(r[10] or 0)])
        n += 1
    except: pass
conn.commit()
print(f"  ✅ daily: +{n}行（共{len(items)}条返回）")

time.sleep(1)

# ── 2. daily_basic 基本面 ─────────────────────────────────────────
print(f"=== 更新 daily_basic ({DATE}) ===")
flds, items = call('daily_basic', {'trade_date': DATE},
    'ts_code,trade_date,turnover_rate,volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,total_share,float_share,free_share,total_mv,circ_mv')
n = 0
for r in items:
    try:
        conn.execute("""INSERT OR REPLACE INTO daily_basic
            (ts_code,trade_date,turnover_rate,volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,total_share,float_share,free_share,total_mv,circ_mv)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [r[0],r[1]] + [float(x or 0) if x else None for x in r[2:]])
        n += 1
    except: pass
conn.commit()
print(f"  ✅ daily_basic: +{n}行（共{len(items)}条返回）")

conn.close()

# 验证
conn2 = sqlite3.connect(DB)
cur = conn2.cursor()
cur.execute("SELECT MAX(trade_date) FROM daily")
print(f"\n验证 daily 最新: {cur.fetchone()[0]}")
cur.execute("SELECT MAX(trade_date) FROM daily_basic")
print(f"验证 daily_basic 最新: {cur.fetchone()[0]}")
conn2.close()
print("\n完成")
