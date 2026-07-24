#!/usr/bin/env python3
"""按交易日批量下载2016-2019数据"""
import requests, json, sqlite3, time, sys

TOKEN = '2b6b1b830a45468b9856e6500ce40a90'
BASE = 'https://ts.gyzcloud.top/api'
DB = '/Users/ziruzhu/stock-data/stock_all.db'

def download_range(api, table, fields, start, end, map_fn):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    print(f'\n{api} -> {table} ({start} ~ {end})', flush=True)
    
    # 获取交易日历
    r = requests.post(f'{BASE}/trade_cal', json={
        'api_name':'trade_cal','token':TOKEN,
        'params':{'exchange':'SSE','start_date':start,'end_date':end,'is_open':1},
        'fields':'cal_date'
    }, timeout=10)
    dates = [d[0] for d in r.json().get('data',{}).get('items',[])]
    total = len(dates)
    print(f'{total}个交易日', flush=True)
    
    rows = 0; calls = 0
    for i, dt in enumerate(dates):
        if calls >= 140:
            time.sleep(70)
            calls = 0
        
        try:
            r = requests.post(f'{BASE}/{api}', json={
                'api_name': api, 'token': TOKEN,
                'params': {'trade_date': dt},
                'fields': fields
            }, timeout=8)
            calls += 1
            
            if r.status_code != 200: continue
            items = r.json().get('data', {}).get('items', [])
            if not items: continue
            
            n = 0
            for row in items:
                try:
                    vals = map_fn(row)
                    cur.execute(f"INSERT OR IGNORE INTO {table} VALUES({','.join('?'*len(vals))})", vals)
                    n += 1
                except: pass
            rows += n
        except: pass
        
        if (i+1) % 50 == 0:
            conn.commit()
            sys.stdout.write('.')
            sys.stdout.flush()
        time.sleep(0.05)
    
    conn.commit()
    conn.close()
    print(f' {rows}行')

# 1. 日线
download_range('daily', 'daily',
    'ts_code,trade_date,open,high,low,close,vol,amount',
    '20160101','20191231',
    lambda r: [r[0],r[1],float(r[2]or 0),float(r[3]or 0),float(r[4]or 0),float(r[5]or 0),float(r[6]or 0),float(r[7]or 0)])

# 2. 资金流
download_range('moneyflow', 'moneyflow',
    'ts_code,trade_date,buy_sm_vol,buy_sm_amount,sell_sm_vol,sell_sm_amount,buy_md_vol,buy_md_amount,sell_md_vol,sell_md_amount,buy_lg_vol,buy_lg_amount,sell_lg_vol,sell_lg_amount,buy_elg_vol,buy_elg_amount,sell_elg_vol,sell_elg_amount,net_mf_vol,net_mf_amount',
    '20160101','20191231',
    lambda r: [str(v or '') for v in r])

# 3. 复权因子
download_range('adj_factor', 'stk_factor',
    'ts_code,trade_date,adj_factor',
    '20160101','20191231',
    lambda r: [r[0],r[1],float(r[2]or 0)])

# 4. 日线指标
download_range('daily_basic', 'daily_basic',
    'ts_code,trade_date,turnover_rate,volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,total_share,float_share,free_share,total_mv,circ_mv',
    '20160101','20191231',
    lambda r: [r[0],r[1],float(r[2]or 0),float(r[3]or 0),float(r[4]or 0),float(r[5]or 0),float(r[6]or 0),float(r[7]or 0),float(r[8]or 0),float(r[9]or 0),float(r[10]or 0),float(r[11]or 0),float(r[12]or 0),float(r[13]or 0)])

print('\n全完成')
