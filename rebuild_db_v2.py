#!/usr/bin/env python3
"""
快速重建 stock_all.db — 只下载daily_scan.py需要的300只股票数据
策略：先拉最新一天全市场数据排序Top300，再逐只下载250日历史
"""
import sqlite3, requests, time, os
from datetime import datetime

DB = os.path.expanduser('~/stock-data/stock_all.db')
TOKEN = '2b6b1b830a45468b9856e6500ce40a90'
BASE = 'https://ts.gyzcloud.top/api'

RATE, PAUSE = 100, 65  # daily API很慢，降低并发

def api(api_name, params, fields=None, timeout=180):
    """调用gycloud API"""
    body = {'api_name': api_name, 'token': TOKEN, 'params': params}
    if fields:
        body['fields'] = fields
    for attempt in range(3):
        try:
            r = requests.post(BASE, json=body, timeout=timeout)
            d = r.json()
            if d.get('code') == 0:
                return d['data'].get('fields', []), d['data'].get('items', [])
            if d.get('code') == -2001:
                print(f"  限流, 等{PAUSE}s...", flush=True)
                time.sleep(PAUSE)
                continue
            print(f"  API err: {d.get('msg','?')}")
            return None, None
        except Exception as e:
            if attempt < 2:
                time.sleep(5)
            else:
                print(f"  API timeout: {e}")
                return None, None
    return None, None

# ========== Step 1: stock_list ==========
print("1/4 下载 stock_list...")
conn = sqlite3.connect(DB)
conn.execute("DROP TABLE IF EXISTS stock_list")
conn.commit()
fields, items = api('stock_basic', {'list_status': 'L'},
    'ts_code,symbol,name,area,industry,market,list_date')
pf = ','.join(fields)
ph = ','.join(['?']*len(fields))
conn.execute(f"CREATE TABLE IF NOT EXISTS stock_list ({','.join(f+' TEXT' for f in fields)}, PRIMARY KEY(ts_code))")
conn.executemany(f"INSERT OR REPLACE INTO stock_list({pf}) VALUES({ph})", items)
conn.commit()
print(f"  stock_list: {len(items)} 只")

# ========== Step 2: 拿到最新交易日期 + 当天全市场数据 ==========
print("\n2/4 获取最新交易日 + 全市场日线...")
fields_d, items_d = api('daily', {'trade_date': ''},
    'ts_code,trade_date,open,high,low,close,vol', timeout=180)
if not items_d:
    print("ERROR: 获取全市场日线失败")
    conn.close()
    exit(1)

latest_date = items_d[0][1]
print(f"  最新交易日: {latest_date} | 股票数: {len(items_d)}")

# 建 daily 表
conn.execute("""CREATE TABLE IF NOT EXISTS daily (
    ts_code TEXT, trade_date TEXT, open REAL, high REAL, low REAL, close REAL, vol REAL,
    PRIMARY KEY(ts_code, trade_date))""")
conn.executemany("INSERT OR REPLACE INTO daily(ts_code,trade_date,open,high,low,close,vol) VALUES(?,?,?,?,?,?,?)",
    [(r[0], r[1], float(r[2] or 0), float(r[3] or 0), float(r[4] or 0), float(r[5] or 0), float(r[6] or 0)) for r in items_d])
conn.commit()
print(f"  写入 {len(items_d)} 条")

# 按成交量排序 Top300
top300 = sorted(items_d, key=lambda x: float(x[6] or 0), reverse=True)[:300]
top_codes = [t[0] for t in top300]
print(f"  Top300 已选出")

# ========== Step 3: 逐只下载Top300的历史数据 ==========
print(f"\n3/4 下载Top300历史日线...")
start_date = '20250701'  # 从2025年7月开始，覆盖250日
reqs = tn = 0
t0 = time.time()
for i, code in enumerate(top_codes):
    f, items = api('daily', {'ts_code': code, 'start_date': start_date},
        'ts_code,trade_date,open,high,low,close,vol', timeout=30)
    reqs += 1
    if items:
        conn.executemany("INSERT OR REPLACE INTO daily(ts_code,trade_date,open,high,low,close,vol) VALUES(?,?,?,?,?,?,?)",
            [(r[0], r[1], float(r[2] or 0), float(r[3] or 0), float(r[4] or 0), float(r[5] or 0), float(r[6] or 0)) for r in items])
        tn += len(items)
        conn.commit()

    if (i+1) % 30 == 0:
        e = (time.time()-t0)/60
        eta = e/(i+1)*(300-i-1)
        print(f"  [{i+1}/300] {e:.0f}m ETA:{eta:.0f}m | +{tn}条", flush=True)

    if reqs > 0 and reqs % RATE == 0:
        print(f"  --- 限速暂停 {PAUSE}s ---", flush=True)
        time.sleep(PAUSE)

e = (time.time()-t0)/60
print(f"  Top300 完成: {e:.0f}m | {tn}条历史数据")

# ========== Step 4: moneyflow ==========
print(f"\n4/4 下载资金流向 (最近120天)...")
r = requests.post(BASE, json={
    'api_name': 'trade_cal', 'token': TOKEN,
    'params': {'exchange': 'SSE', 'start_date': '20250101', 'end_date': datetime.now().strftime('%Y%m%d'), 'is_open': 1},
    'fields': 'cal_date'
}, timeout=10)
dates = sorted([d[0] for d in r.json()['data']['items']])
dates = dates[-120:]

conn.execute("""CREATE TABLE IF NOT EXISTS moneyflow (
    ts_code TEXT, trade_date TEXT,
    buy_sm_vol TEXT, sell_sm_vol TEXT, buy_sm_amount TEXT, sell_sm_amount TEXT,
    buy_md_vol TEXT, sell_md_vol TEXT, buy_md_amount TEXT, sell_md_amount TEXT,
    buy_lg_vol TEXT, sell_lg_vol TEXT, buy_lg_amount TEXT, sell_lg_amount TEXT,
    buy_elg_vol TEXT, sell_elg_vol TEXT, buy_elg_amount TEXT, sell_elg_amount TEXT,
    net_mf_vol TEXT, net_mf_amount TEXT, PRIMARY KEY(ts_code, trade_date))""")
conn.commit()

try:
    existing = {r[0] for r in conn.execute("SELECT DISTINCT trade_date FROM moneyflow").fetchall()}
except:
    existing = set()
pending = [d for d in dates if d not in existing]

mf_reqs = mf_tn = 0
for i, d in enumerate(pending):
    f, items = api('moneyflow', {'trade_date': d}, timeout=30)
    mf_reqs += 1
    if items:
        pf = ','.join(f)
        ph = ','.join(['?']*len(f))
        conn.executemany(f"INSERT OR REPLACE INTO moneyflow({pf}) VALUES({ph})", items)
        mf_tn += len(items)
        conn.commit()
    if (i+1) % 10 == 0:
        print(f"  moneyflow [{i+1}/{len(pending)}] +{mf_tn}条", flush=True)
    if mf_reqs > 0 and mf_reqs % RATE == 0:
        time.sleep(PAUSE)

# 统计
print("\n" + "="*50)
stats = conn.execute("SELECT COUNT(DISTINCT ts_code), COUNT(*) FROM daily").fetchone()
print(f"daily: {stats[0]}只, {stats[1]}条")
mf_stats = conn.execute("SELECT COUNT(DISTINCT ts_code), COUNT(*) FROM moneyflow").fetchone()
print(f"moneyflow: {mf_stats[0]}只, {mf_stats[1]}条")
sl_stats = conn.execute("SELECT COUNT(*) FROM stock_list").fetchone()[0]
print(f"stock_list: {sl_stats}只")
db_size = os.path.getsize(DB) / 1024 / 1024
print(f"DB大小: {db_size:.1f}MB")
print("="*50)

conn.close()
print("\nDone! 可以运行 daily_scan.py 了")
