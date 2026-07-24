#!/usr/bin/env python3
"""Air日更新: 补最新交易日数据"""
import requests, json, sqlite3, time, sys

TOKEN = '2b6b1b830a45468b9856e6500ce40a90'
BASE = 'https://ts.gyzcloud.top/api'
DB = '/Users/ziruzhu/stock-data/stock_all.db'

def update_one(api, table, fields, key_cols, map_fn):
    conn = sqlite3.connect(DB)
    conn.execute('PRAGMA journal_mode=DELETE')
    cur = conn.cursor()
    
    # 找最新日期
    try:
        cur.execute(f"SELECT MAX(trade_date) FROM {table}")
        last = cur.fetchone()[0]
    except: last = '20200101'
    
    r = requests.post(f'{BASE}/{api}', json={
        'api_name': api, 'token': TOKEN,
        'params': {'start_date': last, 'end_date': ''},
        'fields': fields
    }, timeout=10)
    
    if r.status_code == 200:
        d = r.json()
        items = d.get('data', {}).get('items', [])
        n = 0
        for row in items:
            vals = map_fn(row) if map_fn else row
            placeholders = ','.join('?' * len(vals))
            try:
                cur.execute(f"INSERT OR REPLACE INTO {table} VALUES({placeholders})", vals)
                n += 1
            except: pass
        conn.commit()
        conn.close()
        return n
    conn.close()
    return 0

# 日线
n1 = update_one('daily', 'daily',
    'ts_code,trade_date,open,high,low,close,vol,amount',
    None, lambda r: [r[0],r[1],float(r[2]or 0),float(r[3]or 0),float(r[4]or 0),float(r[5]or 0),float(r[6]or 0),float(r[7]or 0)])
print(f'daily: +{n1}行')

# 资金流
n2 = update_one('moneyflow', 'moneyflow',
    'ts_code,trade_date,buy_sm_vol,buy_sm_amount,sell_sm_vol,sell_sm_amount,buy_md_vol,buy_md_amount,sell_md_vol,sell_md_amount,buy_lg_vol,buy_lg_amount,sell_lg_vol,sell_lg_amount,buy_elg_vol,buy_elg_amount,sell_elg_vol,sell_elg_amount,net_mf_vol,net_mf_amount',
    None, lambda r: r)
print(f'moneyflow: +{n2}行')

# 复权因子
n3 = update_one('adj_factor', 'stk_factor',
    'ts_code,trade_date,adj_factor',
    None, lambda r: [r[0],r[1],float(r[2]or 0)])
print(f'stk_factor: +{n3}行')

# 日线指标
n4 = update_one('daily_basic', 'daily_basic',
    'ts_code,trade_date,turnover_rate,volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,total_share,float_share,free_share,total_mv,circ_mv',
    None, lambda r: [r[0],r[1],float(r[2]or 0),float(r[3]or 0),float(r[4]or 0),float(r[5]or 0),float(r[6]or 0),float(r[7]or 0),float(r[8]or 0),float(r[9]or 0),float(r[10]or 0),float(r[11]or 0),float(r[12]or 0),float(r[13]or 0)])
print(f'daily_basic: +{n4}行')

print(f'\n更新完成: {n1+n2+n3+n4}行')
