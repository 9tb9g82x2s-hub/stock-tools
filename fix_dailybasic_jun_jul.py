#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补 daily_basic 表 6/30~7/17 数据 (按列名精确INSERT, 修复列数不匹配问题)
API返回15列: ts_code,trade_date,turnover_rate,turnover_rate_f,volume_ratio,
             pe,pe_ttm,pb,ps,ps_ttm,total_share,float_share,free_share,total_mv,circ_mv
表有18列, 缺 close/dv_ratio/dv_ttm, 用带列名INSERT只填API有的15列, 其余留NULL
铁律: INSERT OR IGNORE, 绝不DELETE/DROP
"""
import sqlite3, time, requests

TOKEN = '2b6b1b830a45468b9856e6500ce40a90'
BASE = 'https://ts.gyzcloud.top/api'
DB = '/Users/ziruzhu/stock-data/stock_all.db'
START, END = '20260627', '20260717'

# API返回的字段顺序 = INSERT的列顺序
API_FIELDS = ('ts_code,trade_date,turnover_rate,turnover_rate_f,volume_ratio,'
              'pe,pe_ttm,pb,ps,ps_ttm,total_share,float_share,free_share,total_mv,circ_mv')
COLS = API_FIELDS.split(',')

def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

con = sqlite3.connect(DB)
con.execute('PRAGMA journal_mode=DELETE')
cur = con.cursor()

# 真实交易日 (从daily表取)
cur.execute("SELECT DISTINCT trade_date FROM daily WHERE trade_date>=? AND trade_date<=? ORDER BY trade_date", (START, END))
all_dates = [r[0] for r in cur.fetchall()]
# 已有的
cur.execute("SELECT DISTINCT trade_date FROM daily_basic WHERE trade_date>=? AND trade_date<=?", (START, END))
have = set(r[0] for r in cur.fetchall())
todo = [d for d in all_dates if d not in have]
log(f"待补 daily_basic {len(todo)} 天: {todo}")

placeholders = ','.join('?' * len(COLS))
col_names = ','.join(COLS)
insert_sql = f"INSERT OR IGNORE INTO daily_basic ({col_names}) VALUES({placeholders})"

total = 0
failed = []
for i, dt in enumerate(todo):
    ok = False
    for attempt in range(3):
        try:
            r = requests.post(f'{BASE}/daily_basic', json={
                'api_name': 'daily_basic', 'token': TOKEN,
                'params': {'start_date': dt, 'end_date': dt},
                'fields': API_FIELDS
            }, timeout=20)
            if r.status_code != 200:
                time.sleep(1); continue
            items = r.json().get('data', {}).get('items', [])
            if items:
                # 只保留前15个字段(与COLS对齐), 防止API多返回
                clean = [row[:len(COLS)] for row in items]
                cur.executemany(insert_sql, clean)
                con.commit()
                total += len(clean)
            ok = True
            break
        except Exception as e:
            log(f"  {dt} 异常: {e}")
            time.sleep(1)
    status = 'OK' if ok else 'FAIL'
    if not ok: failed.append(dt)
    log(f"  [{i+1}/{len(todo)}] {dt} {status} 累计{total}行")
    time.sleep(0.1)

log(f"daily_basic 补入 {total} 行, 失败 {failed}")
cur.execute("SELECT MIN(trade_date),MAX(trade_date),COUNT(*) FROM daily_basic")
log(f"daily_basic 现状: {cur.fetchone()}")
con.close()
