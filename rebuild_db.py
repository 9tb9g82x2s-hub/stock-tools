#!/usr/bin/env python3
"""快速重建 stock_all.db 的 stock_list 和 income 表"""
import sqlite3, requests, os

DB = os.path.expanduser('~/stock-data/stock_all.db')
TOKEN = '2b6b1b830a45468b9856e6500ce40a90'
BASE = 'https://ts.gyzcloud.top/api'

def call_api(api_name, params, fields):
    r = requests.post(BASE, json={
        'api_name': api_name, 'token': TOKEN,
        'params': params, 'fields': fields
    }, timeout=30)
    d = r.json()
    if d.get('code') == 0:
        return d['data']['fields'], d['data']['items']
    print(f"API error: {d.get('msg','?')}")
    return None, None

conn = sqlite3.connect(DB)

# 1. stock_list
print("下载 stock_list...")
fields, items = call_api('stock_basic', {'list_status': 'L'},
    'ts_code,symbol,name,area,industry,market,list_date')
if items:
    cols = [f.split(',') for f in ['ts_code TEXT PRIMARY KEY'] + [f'{x} TEXT' for x in fields[1:]]]
    # simpler: all TEXT
    pf = ','.join(fields)
    ph = ','.join(['?']*len(fields))
    conn.execute(f"CREATE TABLE IF NOT EXISTS stock_list ({','.join(f+' TEXT' for f in fields)}, PRIMARY KEY(ts_code))")
    conn.executemany(f"INSERT OR REPLACE INTO stock_list({pf}) VALUES({ph})", items)
    conn.commit()
    print(f"  stock_list: {len(items)} 条")

# 2. income (20251231 年报)
print("下载 income (20251231)...")
fields, items = call_api('income', {'end_date': '20251231'},
    'ts_code,ann_date,f_ann_date,end_date,report_type,comp_type,n_income_attr_p')
if items:
    pf2 = ','.join(fields)
    ph2 = ','.join(['?']*len(fields))
    conn.execute(f"CREATE TABLE IF NOT EXISTS income ({','.join(f+' TEXT' for f in fields)}, PRIMARY KEY(ts_code,end_date,report_type))")
    conn.executemany(f"INSERT OR REPLACE INTO income({pf2}) VALUES({ph2})", items)
    conn.commit()
    print(f"  income: {len(items)} 条")

conn.close()
print("Done.")
