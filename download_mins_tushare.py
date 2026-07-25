#!/usr/bin/env python3
"""
5/15分钟线下载 — Tushare版 (Studio)
用 curl 替代 python requests 绕过 SSL 问题
"""
import subprocess, json, sqlite3, time, os, sys

DB = os.path.expanduser('~/stock-data/stock_all.db')
TOKEN = '2b6b1b830a45468b9856e6500ce40a90'
API = 'https://ts.gyzcloud.top/api'
DAYS = 5  # 下载最近几个交易日

# 查最近交易日
conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.execute("SELECT DISTINCT trade_date FROM daily ORDER BY trade_date DESC LIMIT 10")
trade_dates = [r[0] for r in cur.fetchall()][:DAYS][::-1]  # 正序
print(f"下载日期: {trade_dates}")

# 取所有股票
cur.execute("SELECT DISTINCT ts_code FROM daily WHERE trade_date >= ?", (trade_dates[-2],))
stocks = [r[0] for r in cur.fetchall()]
print(f"股票数: {len(stocks)}")

# 确保表存在
for tbl in ['stk_5min', 'stk_15min']:
    cur.execute(f"""CREATE TABLE IF NOT EXISTS {tbl} (
        ts_code TEXT, trade_date TEXT, trade_time TEXT,
        open REAL, high REAL, low REAL, close REAL, vol REAL, amount REAL,
        PRIMARY KEY(ts_code, trade_date, trade_time)
    )""")
conn.commit()

def call_api(ts_code, freq, start_date, end_date):
    """调用 stk_mins API"""
    cmd = [
        'curl', '-s', '--compressed', '-X', 'POST', API,
        '-H', 'Content-Type: application/json',
        '-d', json.dumps({
            'api_name': 'stk_mins',
            'token': TOKEN,
            'params': {
                'ts_code': ts_code,
                'freq': freq,
                'start_date': start_date,
                'end_date': end_date
            },
            'fields': 'ts_code,trade_time,open,close,high,low,vol,amount'
        })
    ]
    for attempt in range(3):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            d = json.loads(result.stdout)
            if d.get('code') == 0 and d.get('data'):
                return d['data']['items']
            elif d.get('code') == -2001:  # 限流
                print(f'  ⏳ 限流, 等{d.get("msg","60")}秒...')
                time.sleep(60)
        except subprocess.TimeoutExpired:
            if attempt < 2:
                time.sleep(5)
        except:
            pass
    return []

def insert_items(cur, tbl, items):
    """批量插入, 把 trade_time 拆成 trade_date + trade_time"""
    for item in items:
        # API返回顺序: ts_code, trade_time, open, close, high, low, vol, amount
        # 表列顺序:     ts_code, trade_date, trade_time, open, high, low, close, vol, amount
        ts_code = item[0]
        trade_dt = item[1][:10]  # "2026-07-09"
        trade_tm = item[1][11:]  # "14:50:00"
        cur.execute(f"INSERT OR REPLACE INTO {tbl} VALUES(?,?,?,?,?,?,?,?,?)",
            (ts_code, trade_dt, trade_tm, item[2], item[4], item[5], item[3], item[6], item[7]))
    conn.commit()

for freq, tbl in [('5min', 'stk_5min'), ('15min', 'stk_15min')]:
    print(f'\n===== {freq} 分钟线 =====')
    total_items = 0
    failed = 0
    
    start_dt = f'{trade_dates[0][:4]}-{trade_dates[0][4:6]}-{trade_dates[0][6:]} 09:30:00'
    end_dt = f'{trade_dates[-1][:4]}-{trade_dates[-1][4:6]}-{trade_dates[-1][6:]} 15:00:00'
    
    for i, ts in enumerate(stocks):
        items = call_api(ts, freq, start_dt, end_dt)
        if items:
            insert_items(cur, tbl, items)
            total_items += len(items)
        else:
            failed += 1
        
        if (i + 1) % 200 == 0:
            pct = (i + 1) / len(stocks) * 100
            print(f'  {i+1}/{len(stocks)} ({pct:.0f}%) | 已写{total_items}条 | 失败{failed}次',
                  flush=True)
        
        time.sleep(0.25)
    
    print(f'  {freq}完成: {total_items}条, 失败{failed}次')

conn.close()
print('\n✅ 全部完成')
