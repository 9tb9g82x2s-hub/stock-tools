#!/usr/bin/env python3
"""
增量补数：stk_factor技术指标表 2016-01-01 ~ 2019-12-31

背景：daily_basic/moneyflow本地库已有2016年起10年数据，唯独stk_factor(MACD/KDJ/RSI/
BOLL/CCI等技术指标)之前只补到2020年起。经测试gycloud接口本身支持2016年数据，
本脚本只做增量补齐，不改动2020年后已有数据。

铁律遵守：
  - 全程 INSERT OR IGNORE，绝不 DELETE/DROP/覆盖
  - 按 trade_date 循环（而非按股票循环），220天/年 x 4年 = 约980个交易日，效率最优
  - 断点续传：任意时刻中断，重新运行会自动跳过已有(ts_code,trade_date)组合

用法：
  /Users/ziruzhu/stock-tools/.venv/bin/python download_stkfactor_2016_2019.py
"""
import sqlite3
import time
import sys
import requests

TOKEN = '2b6b1b830a45468b9856e6500ce40a90'
BASE_URL = 'https://ts.gyzcloud.top/api'
DB = '/Users/ziruzhu/stock-data/stock_all.db'
START_DATE = '20160101'
END_DATE = '20191231'
RATE_LIMIT_CALLS = 140
RATE_LIMIT_WINDOW = 70
CALL_GAP = 0.05

FIELDS = ('ts_code,trade_date,close,open,high,low,pre_close,change,pct_change,vol,amount,'
          'adj_factor,open_hfq,open_qfq,close_hfq,close_qfq,high_hfq,high_qfq,low_hfq,low_qfq,'
          'pre_close_hfq,pre_close_qfq,macd_dif,macd_dea,macd,kdj_k,kdj_d,kdj_j,'
          'rsi_6,rsi_12,rsi_24,boll_upper,boll_mid,boll_lower,cci')
FIELD_LIST = FIELDS.split(',')

conn = sqlite3.connect(DB)
cur = conn.cursor()

print('获取交易日历（改用本地daily表真实交易日，gycloud的trade_cal is_open过滤已失效会返回全部自然日）...')
cur.execute("SELECT DISTINCT trade_date FROM daily WHERE trade_date>=? AND trade_date<=?",
            (START_DATE, END_DATE))
all_dates = sorted(r[0] for r in cur.fetchall())
print(f'共{len(all_dates)}个真实交易日 ({START_DATE}~{END_DATE})')

cur.execute("SELECT DISTINCT trade_date FROM stk_factor WHERE trade_date>=? AND trade_date<=?",
            (START_DATE, END_DATE))
existing_dates = set(r[0] for r in cur.fetchall())
todo_dates = [d for d in all_dates if d not in existing_dates]
print(f'已有{len(existing_dates)}天, 待补{len(todo_dates)}天')

if not todo_dates:
    print('无需补数, 全部已存在')
    conn.close()
    sys.exit(0)

placeholders = ','.join('?' * len(FIELD_LIST))
insert_sql = f"INSERT OR IGNORE INTO stk_factor VALUES({placeholders})"

t0 = time.time()
calls = 0
total_rows = 0
failed_dates = []

for i, dt in enumerate(todo_dates):
    if calls >= RATE_LIMIT_CALLS:
        print(f'  达到速率上限, 休眠{RATE_LIMIT_WINDOW}秒 ...')
        time.sleep(RATE_LIMIT_WINDOW)
        calls = 0

    ok = False
    req_t0 = time.time()
    for attempt in range(3):
        try:
            r = requests.post(f'{BASE_URL}/stk_factor', json={
                'api_name': 'stk_factor', 'token': TOKEN,
                'params': {'trade_date': dt}, 'fields': FIELDS
            }, timeout=10)
            calls += 1
            if r.status_code != 200:
                time.sleep(1)
                continue
            data = r.json()
            items = data.get('data', {}).get('items', [])
            if items:
                cur.executemany(insert_sql, items)
                total_rows += len(items)
            ok = True
            break
        except Exception as e:
            time.sleep(1)
            continue
    req_elapsed = time.time() - req_t0

    if not ok:
        failed_dates.append(dt)

    conn.commit()
    elapsed = time.time() - t0
    print(f'[{i+1}/{len(todo_dates)}] {dt} 本次{req_elapsed:.1f}s {"OK" if ok else "FAIL"} '
          f'累计{total_rows}行 总耗时{elapsed:.0f}s', flush=True)

    time.sleep(CALL_GAP)

conn.commit()

print(f'\n完成: 补入约{total_rows}行, 耗时{time.time()-t0:.0f}s')
if failed_dates:
    print(f'失败{len(failed_dates)}天(可重跑本脚本自动补): {failed_dates[:20]}')

cur.execute("SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM stk_factor")
print('stk_factor现状:', cur.fetchone())
conn.close()
