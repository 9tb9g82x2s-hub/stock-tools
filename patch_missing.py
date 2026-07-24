#!/usr/bin/env python3
"""
补全 stock_all.db 遗漏表数据
- ggt_daily: 07-20 → 07-21
- top_list:  07-20 → 07-21
- margin:    07-17 → 07-21（SSE/SZSE/BSE三交易所）
- margin_detail: 07-17 → 07-21
"""
import requests, sqlite3, time

TOKEN = '2b6b1b830a45468b9856e6500ce40a90'
BASE  = 'https://ts.gyzcloud.top/api'
DB    = '/Users/ziruzhu/stock-data/stock_all.db'

def call_ts(api, params, fields, max_retries=3):
    for attempt in range(max_retries):
        try:
            r = requests.post(BASE, json={
                'api_name': api, 'token': TOKEN,
                'params': params, 'fields': fields
            }, timeout=30)
            d = r.json()
            if d.get('code') == 0:
                return d['data']['fields'], d['data']['items']
            msg = d.get('msg', '')
            if '频繁' in msg or '稍后' in msg:
                wait = (attempt + 1) * 30
                print(f"  频控，等{wait}秒...")
                time.sleep(wait)
            else:
                raise Exception(f"API错误: {msg}")
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(5)
    raise Exception("重试失败")

conn = sqlite3.connect(DB)
conn.execute('PRAGMA journal_mode=WAL')

# ── 1. ggt_daily 港股通日线 ───────────────────────────────────────
print("=== 1. ggt_daily 港股通 ===")
cur = conn.cursor()
cur.execute("SELECT MAX(trade_date) FROM ggt_daily")
last = cur.fetchone()[0] or '20260101'
try:
    flds, items = call_ts('ggt_daily', {'start_date': last, 'end_date': '20260721'},
        'trade_date,buy_amount,buy_volume,sell_amount,sell_volume')
    n = 0
    for r in items:
        if r[0] > last:
            try:
                conn.execute("INSERT OR REPLACE INTO ggt_daily VALUES(?,?,?,?,?)",
                    [r[0], float(r[1] or 0), float(r[2] or 0),
                     float(r[3] or 0), float(r[4] or 0)])
                n += 1
            except: pass
    conn.commit()
    print(f"  ✅ ggt_daily: +{n}行，最新={conn.execute('SELECT MAX(trade_date) FROM ggt_daily').fetchone()[0]}")
except Exception as e:
    print(f"  ❌ ggt_daily失败: {e}")

time.sleep(1)

# ── 2. top_list 龙虎榜 ───────────────────────────────────────────
print("=== 2. top_list 龙虎榜 ===")
cur.execute("SELECT MAX(trade_date) FROM top_list")
last = cur.fetchone()[0] or '20260101'
new_dates = ['20260721']  # 只差07-21
n = 0
for date in new_dates:
    if date <= last:
        continue
    try:
        flds, items = call_ts('top_list', {'trade_date': date},
            'trade_date,ts_code,name,close,pct_change,turnover_rate,amount,l_sell,l_buy,l_amount,net_amount,net_rate,amount_rate,float_values,reason')
        for r in items:
            try:
                conn.execute("""INSERT OR REPLACE INTO top_list
                    (trade_date,ts_code,name,close,pct_change,turnover_rate,amount,
                     l_sell,l_buy,l_amount,net_amount,net_rate,amount_rate,float_values,reason)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [r[0], r[1], r[2],
                     float(r[3] or 0) if r[3] else None,
                     float(r[4] or 0) if r[4] else None,
                     float(r[5] or 0) if r[5] else None,
                     float(r[6] or 0) if r[6] else None,
                     float(r[7] or 0) if r[7] else None,
                     float(r[8] or 0) if r[8] else None,
                     float(r[9] or 0) if r[9] else None,
                     float(r[10] or 0) if r[10] else None,
                     float(r[11] or 0) if r[11] else None,
                     float(r[12] or 0) if r[12] else None,
                     float(r[13] or 0) if r[13] else None,
                     r[14]])
                n += 1
            except: pass
        print(f"  {date}: +{len(items)}条")
    except Exception as e:
        print(f"  {date} 失败: {e}")
    time.sleep(0.5)
conn.commit()
print(f"  ✅ top_list: 共+{n}行")

time.sleep(1)

# ── 3. margin 两融汇总（三交易所）────────────────────────────────
print("=== 3. margin 两融汇总 ===")
cur.execute("SELECT MAX(trade_date) FROM margin")
last = cur.fetchone()[0] or '20260101'
new_dates = ['20260718', '20260720', '20260721']
for exchange in ['SSE', 'SZSE', 'BSE']:
    n = 0
    for date in new_dates:
        if date <= last:
            continue
        try:
            flds, items = call_ts('margin', {'trade_date': date, 'exchange_id': exchange},
                'trade_date,exchange_id,rzye,rzmre,rzche,rqye,rqmcl,rzrqye,rqyl')
            for r in items:
                try:
                    conn.execute("""INSERT OR REPLACE INTO margin
                        (trade_date,exchange_id,rzye,rzmre,rzche,rqye,rqmcl,rzrqye,rqyl)
                        VALUES(?,?,?,?,?,?,?,?,?)""",
                        [r[0], r[1],
                         float(r[2] or 0) if r[2] else None,
                         float(r[3] or 0) if r[3] else None,
                         float(r[4] or 0) if r[4] else None,
                         float(r[5] or 0) if r[5] else None,
                         float(r[6] or 0) if r[6] else None,
                         float(r[7] or 0) if r[7] else None,
                         float(r[8] or 0) if r[8] else None])
                    n += 1
                except: pass
        except Exception as e:
            print(f"  {exchange} {date} 失败: {e}")
        time.sleep(0.3)
    if n > 0:
        print(f"  {exchange}: +{n}行")
conn.commit()
print(f"  ✅ margin 最新={conn.execute('SELECT MAX(trade_date) FROM margin').fetchone()[0]}")

time.sleep(1)

# ── 4. margin_detail 两融个股明细 ────────────────────────────────
print("=== 4. margin_detail 两融个股明细 ===")
cur.execute("SELECT MAX(trade_date) FROM margin_detail")
last = cur.fetchone()[0] or '20260101'
new_dates = ['20260718', '20260720', '20260721']
for date in new_dates:
    if date <= last:
        print(f"  {date}: 已有，跳过")
        continue
    try:
        flds, items = call_ts('margin_detail', {'trade_date': date},
            'ts_code,trade_date,rzye,rqye,rzmre,rqyl,rzche,rqchl')
        n = 0
        for r in items:
            try:
                conn.execute("""INSERT OR REPLACE INTO margin_detail
                    (ts_code,trade_date,rzye,rqye,rzmre,rqyl,rzche,rqchl)
                    VALUES(?,?,?,?,?,?,?,?)""",
                    [r[0], r[1],
                     float(r[2] or 0) if r[2] else None,
                     float(r[3] or 0) if r[3] else None,
                     float(r[4] or 0) if r[4] else None,
                     float(r[5] or 0) if r[5] else None,
                     float(r[6] or 0) if r[6] else None,
                     float(r[7] or 0) if r[7] else None])
                n += 1
            except: pass
        conn.commit()
        print(f"  {date}: +{n}行")
    except Exception as e:
        print(f"  {date} 失败: {e}")
    time.sleep(1)
print(f"  ✅ margin_detail 最新={conn.execute('SELECT MAX(trade_date) FROM margin_detail').fetchone()[0]}")

conn.close()
print("\n========= 全部补全完成 =========")
