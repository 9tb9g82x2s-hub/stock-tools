#!/usr/bin/env python3
"""Studio历史数据补齐: 2016-2019 日线/资金流/指标"""
import requests, json, sqlite3, io, time, sys, pandas as pd

TOKEN = '2b6b1b830a45468b9856e6500ce40a90'
BASE = 'https://ts.gyzcloud.top/api'
DB = '/Users/ziruzhu/stock-data/stock_all.db'

def fetch_all(api, table, fields, key_cols, map_fn=None):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    print(f'\n{api} -> {table}', flush=True)
    
    for y in range(2016, 2020):
        done = 0
        for m in range(1, 13):
            start = f'{y}{m:02d}01'
            end = f'{y}{m:02d}28' if m==2 else f'{y}{m:02d}30'
            if m==12: end = f'{y}1231'
            
            for attempt in range(3):
                try:
                    r = requests.post(f'{BASE}/{api}', json={
                        'api_name': api, 'token': TOKEN,
                        'params': {'start_date': start, 'end_date': end},
                        'fields': fields
                    }, timeout=15)
                    if r.status_code != 200: break
                    d = r.json()
                    items = d.get('data', {}).get('items', [])
                    if not items: break
                    
                    n = 0
                    for row in items:
                        try:
                            vals = map_fn(row) if map_fn else row
                            placeholders = ','.join('?' * len(vals))
                            cur.execute(f"INSERT OR IGNORE INTO {table} VALUES({placeholders})", vals)
                            n += 1
                        except: pass
                    done += n
                    break
                except: time.sleep(1)
            
            time.sleep(0.1)
            sys.stdout.write('.')
            sys.stdout.flush()
        
        conn.commit()
        print(f' {y}:{done}', end='', flush=True)
    
    conn.close()
    print(' 完成')

# 1. 日线
fetch_all('daily', 'daily', 
    'ts_code,trade_date,open,high,low,close,vol,amount',
    ['ts_code','trade_date','open','high','low','close','vol','amount'],
    lambda r: [r[0], r[1], float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[6]), float(r[7])])

# 2. 资金流
fetch_all('moneyflow', 'moneyflow',
    'ts_code,trade_date,buy_sm_vol,buy_sm_amount,sell_sm_vol,sell_sm_amount,buy_md_vol,buy_md_amount,sell_md_vol,sell_md_amount,buy_lg_vol,buy_lg_amount,sell_lg_vol,sell_lg_amount,buy_elg_vol,buy_elg_amount,sell_elg_vol,sell_elg_amount,net_mf_vol,net_mf_amount',
    ['ts_code','trade_date','buy_sm_vol','buy_sm_amount','sell_sm_vol','sell_sm_amount','buy_md_vol','buy_md_amount','sell_md_vol','sell_md_amount','buy_lg_vol','buy_lg_amount','sell_lg_vol','sell_lg_amount','buy_elg_vol','buy_elg_amount','sell_elg_vol','sell_elg_amount','net_mf_vol','net_mf_amount'],
    lambda r: r)

# 3. 复权因子
fetch_all('adj_factor', 'stk_factor',
    'ts_code,trade_date,adj_factor',
    ['ts_code','trade_date','adj_factor'],
    lambda r: [r[0], r[1], float(r[2])])

# 4. 日线指标
fetch_all('daily_basic', 'daily_basic',
    'ts_code,trade_date,turnover_rate,volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,total_share,float_share,free_share,total_mv,circ_mv',
    ['ts_code','trade_date','turnover_rate','volume_ratio','pe','pe_ttm','pb','ps','ps_ttm','total_share','float_share','free_share','total_mv','circ_mv'],
    lambda r: [r[0],r[1],float(r[2]),float(r[3]),float(r[4]),float(r[5]),float(r[6]),float(r[7]),float(r[8]),float(r[9]),float(r[10]),float(r[11]),float(r[12]),float(r[13])])

print('\n全完成')
