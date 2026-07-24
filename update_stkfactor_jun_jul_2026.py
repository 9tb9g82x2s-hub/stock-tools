#!/usr/bin/env python3
"""
增量补数：stk_factor技术指标表 2026-06-27 ~ 2026-07-17
逻辑完全复用 download_stkfactor_2016_2019.py，只改日期范围
铁律：INSERT OR IGNORE，不覆盖已有数据
断点续传：随时可中断重跑，自动跳过已有交易日
"""
import sqlite3, time, sys, requests

TOKEN   = '2b6b1b830a45468b9856e6500ce40a90'
BASE_URL = 'https://ts.gyzcloud.top/api'
DB      = '/Users/ziruzhu/stock-data/stock_all.db'
START_DATE = '20260627'
END_DATE   = '20260717'

RATE_LIMIT_CALLS  = 140
RATE_LIMIT_WINDOW = 70
CALL_GAP = 0.05

FIELDS = ('ts_code,trade_date,close,open,high,low,pre_close,change,pct_change,vol,amount,'
          'adj_factor,open_hfq,open_qfq,close_hfq,close_qfq,high_hfq,high_qfq,low_hfq,low_qfq,'
          'pre_close_hfq,pre_close_qfq,macd_dif,macd_dea,macd,kdj_k,kdj_d,kdj_j,'
          'rsi_6,rsi_12,rsi_24,boll_upper,boll_mid,boll_lower,cci')
FIELD_LIST = FIELDS.split(',')

conn = sqlite3.connect(DB)
conn.execute('PRAGMA journal_mode=DELETE')
cur  = conn.cursor()

# 从 daily 表取真实交易日（gycloud trade_cal 的 is_open 过滤已失效）
cur.execute("SELECT DISTINCT trade_date FROM daily WHERE trade_date>=? AND trade_date<=?",
            (START_DATE, END_DATE))
all_dates = sorted(r[0] for r in cur.fetchall())
print(f'待补区间真实交易日: {len(all_dates)} 天 ({START_DATE}~{END_DATE})')
print('交易日列表:', all_dates)

# 已有的日期直接跳过
cur.execute("SELECT DISTINCT trade_date FROM stk_factor WHERE trade_date>=? AND trade_date<=?",
            (START_DATE, END_DATE))
existing = set(r[0] for r in cur.fetchall())
todo = [d for d in all_dates if d not in existing]
print(f'已有 {len(existing)} 天，待补 {len(todo)} 天')

if not todo:
    print('无需补数，全部已存在')
    conn.close()
    sys.exit(0)

placeholders = ','.join('?' * len(FIELD_LIST))
insert_sql   = f"INSERT OR IGNORE INTO stk_factor VALUES({placeholders})"

t0    = time.time()
calls = 0
total_rows   = 0
failed_dates = []

for i, dt in enumerate(todo):
    if calls >= RATE_LIMIT_CALLS:
        print(f'  达到速率上限，休眠 {RATE_LIMIT_WINDOW}s ...')
        time.sleep(RATE_LIMIT_WINDOW)
        calls = 0

    ok    = False
    req_t = time.time()
    for attempt in range(3):
        try:
            r = requests.post(f'{BASE_URL}/stk_factor', json={
                'api_name': 'stk_factor', 'token': TOKEN,
                'params': {'trade_date': dt}, 'fields': FIELDS
            }, timeout=15)
            calls += 1
            if r.status_code != 200:
                time.sleep(1)
                continue
            items = r.json().get('data', {}).get('items', [])
            if items:
                cur.executemany(insert_sql, items)
                total_rows += len(items)
            ok = True
            break
        except Exception as e:
            time.sleep(1)

    if not ok:
        failed_dates.append(dt)

    conn.commit()
    elapsed = time.time() - t0
    print(f'[{i+1}/{len(todo)}] {dt} {"OK" if ok else "FAIL"} '
          f'({time.time()-req_t:.1f}s)  累计 {total_rows} 行  总耗时 {elapsed:.0f}s', flush=True)
    time.sleep(CALL_GAP)

conn.commit()
conn.close()

print(f'\n完成: 补入 {total_rows} 行，耗时 {time.time()-t0:.0f}s')
if failed_dates:
    print(f'失败 {len(failed_dates)} 天 (可重跑自动续): {failed_dates}')

# 最终校验
conn2 = sqlite3.connect(DB)
res = conn2.execute("SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM stk_factor").fetchone()
print(f'stk_factor 现状: 最早={res[0]} 最晚={res[1]} 总行数={res[2]}')
conn2.close()
