#!/usr/bin/env python3
"""下载资金流向数据到 stock_all.db — 按交易日循环，最近120天"""
import sqlite3, requests, time, os
from datetime import datetime, timedelta

DB = os.path.expanduser('~/stock-data/stock_all.db')
TOKEN = '2b6b1b830a45468b9856e6500ce40a90'
BASE = 'https://ts.gyzcloud.top/api'
RATE, PAUSE = 140, 65

def api(name, params):
    for _ in range(3):
        try:
            r = requests.post(BASE, json={
                'api_name': name, 'token': TOKEN, 'params': params,
                'fields': 'ts_code,trade_date,buy_sm_vol,sell_sm_vol,buy_sm_amount,sell_sm_amount,buy_md_vol,sell_md_vol,buy_md_amount,sell_md_amount,buy_lg_vol,sell_lg_vol,buy_lg_amount,sell_lg_amount,buy_elg_vol,sell_elg_vol,buy_elg_amount,sell_elg_amount,net_mf_vol,net_mf_amount'
            }, timeout=15)
            d = r.json()
            if d.get('code') == 0:
                return d['data']['fields'], d['data']['items']
            if d.get('code') == -2001:
                time.sleep(10); continue
            return None, None
        except:
            time.sleep(3)
    return None, None

# 获取交易日
r = requests.post(BASE, json={
    'api_name': 'trade_cal', 'token': TOKEN,
    'params': {'exchange': 'SSE', 'start_date': '20250101', 'end_date': datetime.now().strftime('%Y%m%d'), 'is_open': 1},
    'fields': 'cal_date'
}, timeout=10)
dates = sorted([d[0] for d in r.json()['data']['items']])
dates = dates[-120:]  # 最近120个交易日
print(f"资金流向: 下载最近 {len(dates)} 个交易日")

conn = sqlite3.connect(DB)

# 检查已下载
try:
    existing = {r[0] for r in conn.execute("SELECT DISTINCT trade_date FROM moneyflow").fetchall()}
except:
    conn.execute("CREATE TABLE IF NOT EXISTS moneyflow (ts_code TEXT, trade_date TEXT, "
        "buy_sm_vol TEXT, sell_sm_vol TEXT, buy_sm_amount TEXT, sell_sm_amount TEXT, "
        "buy_md_vol TEXT, sell_md_vol TEXT, buy_md_amount TEXT, sell_md_amount TEXT, "
        "buy_lg_vol TEXT, sell_lg_vol TEXT, buy_lg_amount TEXT, sell_lg_amount TEXT, "
        "buy_elg_vol TEXT, sell_elg_vol TEXT, buy_elg_amount TEXT, sell_elg_amount TEXT, "
        "net_mf_vol TEXT, net_mf_amount TEXT, PRIMARY KEY(ts_code, trade_date))")
    conn.commit()
    existing = set()

pending = [d for d in dates if d not in existing]
print(f"  已下载: {len(existing)}天, 待下载: {len(pending)}天")

reqs = tn = 0
t0 = time.time()
for i, d in enumerate(pending):
    fields, items = api('moneyflow', {'trade_date': d})
    reqs += 1
    if items:
        ph = ','.join(['?']*len(fields))
        pf = ','.join(fields)
        conn.executemany(f"INSERT OR REPLACE INTO moneyflow({pf}) VALUES({ph})", items)
        tn += len(items)
        conn.commit()

    if (i+1) % 20 == 0:
        e = (time.time()-t0)/60
        eta = e/(i+1)*(len(pending)-i-1)
        print(f"  [{d}] {i+1}/{len(pending)} | {e:.0f}m ETA:{eta:.0f}m | +{tn}条", flush=True)

    if reqs > 0 and reqs % RATE == 0:
        print(f"  --- 限速暂停 {PAUSE}s ---", flush=True)
        time.sleep(PAUSE)

elapsed = (time.time()-t0)/60
print(f"\n资金流向完成! {elapsed:.0f}m | 请求{reqs} | 新增{tn}条")
conn.close()
