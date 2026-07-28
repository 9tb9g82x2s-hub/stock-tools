#!/usr/bin/env python3
"""
S021 概念数据下载器（gycloud通道 · 断点续跑）
拉两块：
  1. ths_member  概念成分映射（个股↔概念）
  2. ths_daily   概念指数历史日线（2016-2026）
入库 /Users/ziruzhu/stock-data/stock_all.db 的 ths_member / ths_daily 两表。
断点续跑：已完成的概念记录进 ths_progress 表，重启跳过。
"""
import sqlite3, requests, time, os, sys

DB = os.path.expanduser('~/stock-data/stock_all.db')
TOKEN = '2b6b1b830a45468b9856e6500ce40a90'
BASE = 'https://ts.gyzcloud.top/api'
START, END = '20160101', '20260724'
PAUSE = 60  # 限流等待

def api(api_name, params, fields='', timeout=60):
    body = {'api_name': api_name, 'token': TOKEN, 'params': params, 'fields': fields}
    for attempt in range(4):
        try:
            r = requests.post(BASE, json=body, timeout=timeout)
            d = r.json()
            if d.get('code') == 0:
                return d['data'].get('items', [])
            if d.get('code') == -2001:  # 限流
                print(f"    限流,等{PAUSE}s...", flush=True); time.sleep(PAUSE); continue
            print(f"    API err {api_name}: {d.get('msg','?')}"); return None
        except Exception as e:
            if attempt < 3: time.sleep(4)
            else: print(f"    timeout {api_name}: {e}"); return None
    return None

def main():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    # 建表
    c.execute("""CREATE TABLE IF NOT EXISTS ths_member(
        ts_code TEXT, con_code TEXT, con_name TEXT, weight REAL,
        PRIMARY KEY(ts_code, con_code))""")
    c.execute("""CREATE TABLE IF NOT EXISTS ths_daily(
        ts_code TEXT, trade_date TEXT, close REAL, pct_change REAL,
        PRIMARY KEY(ts_code, trade_date))""")
    c.execute("""CREATE TABLE IF NOT EXISTS ths_progress(
        ts_code TEXT, kind TEXT, PRIMARY KEY(ts_code, kind))""")
    conn.commit()

    # 概念清单：只拉N类题材(炒作主线,分析脚本只用N类)。I行业分类HANDOVER明确不用
    c.execute("SELECT ts_code, name, type FROM concept_index WHERE exchange='A' AND type='N'")
    concepts = c.fetchall()
    print(f"待处理概念: {len(concepts)} 个 (N类题材)", flush=True)

    done_member = {r[0] for r in c.execute("SELECT ts_code FROM ths_progress WHERE kind='member'")}
    done_daily = {r[0] for r in c.execute("SELECT ts_code FROM ths_progress WHERE kind='daily'")}
    print(f"已完成 member:{len(done_member)} daily:{len(done_daily)}", flush=True)

    for i, (code, name, typ) in enumerate(concepts):
        # --- 成分 ---
        if code not in done_member:
            items = api('ths_member', {'ts_code': code}, 'ts_code,con_code,con_name,weight')
            if items is not None:
                rows = [(r[0], r[1], r[2], float(r[3]) if r[3] not in (None,'') else None) for r in items]
                c.executemany("INSERT OR REPLACE INTO ths_member VALUES(?,?,?,?)", rows)
                c.execute("INSERT OR REPLACE INTO ths_progress VALUES(?,'member')", (code,))
                conn.commit()
            time.sleep(0.15)
        # --- 指数日线 ---
        if code not in done_daily:
            items = api('ths_daily', {'ts_code': code, 'start_date': START, 'end_date': END},
                        'ts_code,trade_date,close,pct_change')
            if items is not None:
                rows = [(r[0], r[1], float(r[2]) if r[2] not in (None,'') else None,
                         float(r[3]) if r[3] not in (None,'') else None) for r in items]
                c.executemany("INSERT OR REPLACE INTO ths_daily VALUES(?,?,?,?)", rows)
                c.execute("INSERT OR REPLACE INTO ths_progress VALUES(?,'daily')", (code,))
                conn.commit()
            time.sleep(0.15)

        if (i+1) % 10 == 0:
            print(f"  进度 {i+1}/{len(concepts)}  最近:{name}", flush=True)

    # 汇总
    mc = c.execute("SELECT COUNT(*) FROM ths_member").fetchone()[0]
    mcc = c.execute("SELECT COUNT(DISTINCT ts_code) FROM ths_member").fetchone()[0]
    dc = c.execute("SELECT COUNT(*) FROM ths_daily").fetchone()[0]
    dcc = c.execute("SELECT COUNT(DISTINCT ts_code) FROM ths_daily").fetchone()[0]
    print(f"\n完成! ths_member:{mc}行/{mcc}概念  ths_daily:{dc}行/{dcc}概念", flush=True)
    conn.close()

if __name__ == '__main__':
    main()
