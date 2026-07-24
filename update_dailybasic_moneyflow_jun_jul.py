#!/usr/bin/env python3
"""
增量补数：daily_basic + moneyflow  2026-06-27 ~ 2026-07-17
按交易日逐日查询，INSERT OR IGNORE，断点续传。
"""
import sqlite3, time, sys, requests

TOKEN    = '2b6b1b830a45468b9856e6500ce40a90'
BASE_URL = 'https://ts.gyzcloud.top/api'
DB       = '/Users/ziruzhu/stock-data/stock_all.db'
START    = '20260627'
END      = '20260717'
CALL_GAP = 0.05

# ── daily_basic ──────────────────────────────────────────────────────
DB_FIELDS = ('ts_code,trade_date,turnover_rate,volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,'
             'total_share,float_share,free_share,total_mv,circ_mv')
DB_COLS   = DB_FIELDS.split(',')   # 14列，和表结构对齐（turnover_rate_f 单独算）

# ── moneyflow ─────────────────────────────────────────────────────────
MF_FIELDS = ('ts_code,trade_date,buy_sm_vol,buy_sm_amount,sell_sm_vol,sell_sm_amount,'
             'buy_md_vol,buy_md_amount,sell_md_vol,sell_md_amount,'
             'buy_lg_vol,buy_lg_amount,sell_lg_vol,sell_lg_amount,'
             'buy_elg_vol,buy_elg_amount,sell_elg_vol,sell_elg_amount,'
             'net_mf_vol,net_mf_amount')
MF_COLS   = MF_FIELDS.split(',')   # 20列

def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

conn = sqlite3.connect(DB)
conn.execute('PRAGMA journal_mode=DELETE')
cur  = conn.cursor()

# 获取真实交易日
cur.execute("SELECT DISTINCT trade_date FROM daily WHERE trade_date>=? AND trade_date<=? ORDER BY trade_date",
            (START, END))
all_dates = [r[0] for r in cur.fetchall()]
log(f"区间真实交易日 {len(all_dates)} 天: {all_dates}")

# ═══════════════════════════════════════════════════════
# 1. daily_basic
# ═══════════════════════════════════════════════════════
log("\n── daily_basic ──")
cur.execute("SELECT DISTINCT trade_date FROM daily_basic WHERE trade_date>=? AND trade_date<=?",
            (START, END))
db_done = set(r[0] for r in cur.fetchall())
db_todo = [d for d in all_dates if d not in db_done]
log(f"已有 {len(db_done)} 天，待补 {len(db_todo)} 天")

db_placeholders = ','.join('?'*len(DB_COLS))
db_insert = f"INSERT OR IGNORE INTO daily_basic VALUES({db_placeholders})"

db_total = 0
db_failed = []
for i, dt in enumerate(db_todo):
    ok = False
    for attempt in range(3):
        try:
            r = requests.post(f'{BASE_URL}/daily_basic', json={
                'api_name': 'daily_basic', 'token': TOKEN,
                'params': {'trade_date': dt}, 'fields': DB_FIELDS
            }, timeout=15)
            if r.status_code != 200: time.sleep(1); continue
            items = r.json().get('data', {}).get('items', [])
            if items:
                cur.executemany(db_insert, items)
                db_total += len(items)
            ok = True
            break
        except Exception as e:
            time.sleep(1)
    if not ok:
        # 回退：用 start_date/end_date 区间查（单日）
        try:
            r = requests.post(f'{BASE_URL}/daily_basic', json={
                'api_name': 'daily_basic', 'token': TOKEN,
                'params': {'start_date': dt, 'end_date': dt}, 'fields': DB_FIELDS
            }, timeout=15)
            items = r.json().get('data', {}).get('items', [])
            if items:
                cur.executemany(db_insert, items)
                db_total += len(items)
                ok = True
        except: pass
    conn.commit()
    log(f"  daily_basic [{i+1}/{len(db_todo)}] {dt} {'OK' if ok else 'FAIL'} 累计{db_total}行")
    if not ok: db_failed.append(dt)
    time.sleep(CALL_GAP)

log(f"daily_basic 完成: 补入 {db_total} 行, 失败 {db_failed}")

# ═══════════════════════════════════════════════════════
# 2. moneyflow
# ═══════════════════════════════════════════════════════
log("\n── moneyflow ──")
cur.execute("SELECT DISTINCT trade_date FROM moneyflow WHERE trade_date>=? AND trade_date<=?",
            (START, END))
mf_done = set(r[0] for r in cur.fetchall())
mf_todo = [d for d in all_dates if d not in mf_done]
log(f"已有 {len(mf_done)} 天，待补 {len(mf_todo)} 天")

mf_placeholders = ','.join('?'*len(MF_COLS))
mf_insert = f"INSERT OR IGNORE INTO moneyflow VALUES({mf_placeholders})"

mf_total = 0
mf_failed = []
for i, dt in enumerate(mf_todo):
    ok = False
    for attempt in range(3):
        try:
            r = requests.post(f'{BASE_URL}/moneyflow', json={
                'api_name': 'moneyflow', 'token': TOKEN,
                'params': {'trade_date': dt}, 'fields': MF_FIELDS
            }, timeout=15)
            if r.status_code != 200: time.sleep(1); continue
            items = r.json().get('data', {}).get('items', [])
            if items:
                cur.executemany(mf_insert, items)
                mf_total += len(items)
            ok = True
            break
        except Exception as e:
            time.sleep(1)
    conn.commit()
    log(f"  moneyflow [{i+1}/{len(mf_todo)}] {dt} {'OK' if ok else 'FAIL'} 累计{mf_total}行")
    if not ok: mf_failed.append(dt)
    time.sleep(CALL_GAP)

log(f"moneyflow 完成: 补入 {mf_total} 行, 失败 {mf_failed}")

# ═══════════════════════════════════════════════════════
# 最终校验
# ═══════════════════════════════════════════════════════
log("\n── 最终校验 ──")
for tbl in ['daily_basic', 'moneyflow']:
    row = conn.execute(f"SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM {tbl}").fetchone()
    log(f"  {tbl}: 最早={row[0]}  最晚={row[1]}  总行数={row[2]}")

conn.close()
log("全部完成！")
