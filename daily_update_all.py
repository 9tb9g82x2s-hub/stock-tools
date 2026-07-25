#!/usr/bin/env python3
"""
stock_all.db 日线数据每日增量更新（自动化用）
自动检测每张表的缺口，按交易日历补齐到最新可得交易日。
覆盖表：daily / daily_basic / moneyflow / stk_factor / ggt_daily / top_list / margin / margin_detail
数据源：tushare gycloud HTTP API
"""
import requests, sqlite3, time, sys
from datetime import datetime

TOKEN = '2b6b1b830a45468b9856e6500ce40a90'
BASE  = 'https://ts.gyzcloud.top/api'
DB    = '/Users/ziruzhu/stock-data/stock_all.db'
TODAY = datetime.now().strftime('%Y%m%d')

LOG = []
def log(m):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    LOG.append(line)

def call(api, params, fields, max_retries=3):
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
                time.sleep((attempt + 1) * 30)
            else:
                raise Exception(f"API错误: {msg}")
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(5)
    raise Exception("重试失败")

def get_trade_dates(start, end):
    """获取 (start, end] 之间的交易日（不含start）"""
    _, items = call('trade_cal', {'start_date': start, 'end_date': end, 'is_open': '1'}, 'cal_date')
    return sorted([x[0] for x in items if x[0] > start])

conn = sqlite3.connect(DB)
conn.execute('PRAGMA journal_mode=WAL')
cur = conn.cursor()

log(f"======== stock_all.db 日更 {datetime.now().strftime('%Y-%m-%d %H:%M')} ========")

# 计算需要补的交易日（以 daily 表为基准）
cur.execute("SELECT MAX(trade_date) FROM daily")
base_last = cur.fetchone()[0]
new_dates = get_trade_dates(base_last, TODAY)
if not new_dates:
    log(f"  daily 已最新（{base_last}），无新交易日")
else:
    log(f"  待补交易日: {new_dates}")

# ── 全市场按单日拉的表 ──────────────────────────────────────────
def update_market_table(table, api, cols, fields, floats_from=2):
    cur.execute(f"SELECT MAX(trade_date) FROM {table}")
    last = cur.fetchone()[0] or '20260101'
    dates = get_trade_dates(last, TODAY)
    if not dates:
        log(f"  {table}: 已最新（{last}）")
        return
    total = 0
    for date in dates:
        try:
            _, items = call(api, {'trade_date': date}, fields)
            n = 0
            placeholders = ','.join('?' * len(cols))
            for r in items:
                try:
                    vals = list(r[:2]) + [float(x or 0) if x not in (None, '') else None for x in r[2:len(cols)]]
                    # 前两列(ts_code/trade_date 或 trade_date/exchange)保持原样
                    conn.execute(f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES({placeholders})", vals)
                    n += 1
                except: pass
            conn.commit()
            total += n
        except Exception as e:
            log(f"  {table} {date} 失败: {e}")
        time.sleep(0.4)
    cur.execute(f"SELECT MAX(trade_date) FROM {table}")
    log(f"  ✅ {table}: +{total}行，最新={cur.fetchone()[0]}")

# 1. daily 日线
update_market_table('daily', 'daily',
    ['ts_code','trade_date','open','high','low','close','pre_close','change','pct_chg','vol','amount'],
    'ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount')

# 2. daily_basic 基本面
update_market_table('daily_basic', 'daily_basic',
    ['ts_code','trade_date','turnover_rate','volume_ratio','pe','pe_ttm','pb','ps','ps_ttm','total_share','float_share','free_share','total_mv','circ_mv'],
    'ts_code,trade_date,turnover_rate,volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,total_share,float_share,free_share,total_mv,circ_mv')

# 3. moneyflow 资金流
update_market_table('moneyflow', 'moneyflow',
    ['ts_code','trade_date','buy_sm_vol','buy_sm_amount','sell_sm_vol','sell_sm_amount','buy_md_vol','buy_md_amount','sell_md_vol','sell_md_amount','buy_lg_vol','buy_lg_amount','sell_lg_vol','sell_lg_amount','buy_elg_vol','buy_elg_amount','sell_elg_vol','sell_elg_amount','net_mf_vol','net_mf_amount'],
    'ts_code,trade_date,buy_sm_vol,buy_sm_amount,sell_sm_vol,sell_sm_amount,buy_md_vol,buy_md_amount,sell_md_vol,sell_md_amount,buy_lg_vol,buy_lg_amount,sell_lg_vol,sell_lg_amount,buy_elg_vol,buy_elg_amount,sell_elg_vol,sell_elg_amount,net_mf_vol,net_mf_amount')

# 4. stk_factor 复权因子（特殊：只追加不覆盖，保护指标列）
log(f"  stk_factor: 更新复权因子...")
cur.execute("SELECT MAX(trade_date) FROM stk_factor")
last_sf = cur.fetchone()[0] or '20260101'
dates_sf = get_trade_dates(last_sf, TODAY)
total_sf = 0
for date in dates_sf:
    try:
        _, items = call('adj_factor', {'trade_date': date}, 'ts_code,trade_date,adj_factor')
        n = 0
        for r in items:
            try:
                # 先INSERT新行（ts_code,trade_date,adj_factor三列）
                conn.execute("""INSERT OR IGNORE INTO stk_factor (ts_code, trade_date, adj_factor)
                    VALUES(?,?,?)""", [r[0], r[1], float(r[2] or 0)])
                # 再确保adj_factor是最新的（已存在的行只更新adj_factor列）
                conn.execute("""UPDATE stk_factor SET adj_factor=? WHERE ts_code=? AND trade_date=?""",
                    [float(r[2] or 0), r[0], r[1]])
                n += 1
            except: pass
        conn.commit()
        total_sf += n
    except Exception as e:
        log(f"  stk_factor {date} 失败: {e}")
    time.sleep(0.4)
cur.execute("SELECT MAX(trade_date) FROM stk_factor")
log(f"  ✅ stk_factor: +{total_sf}行，最新={cur.fetchone()[0]}")

# 5. top_list 龙虎榜
update_market_table('top_list', 'top_list',
    ['trade_date','ts_code','name','close','pct_change','turnover_rate','amount','l_sell','l_buy','l_amount','net_amount','net_rate','amount_rate','float_values','reason'],
    'trade_date,ts_code,name,close,pct_change,turnover_rate,amount,l_sell,l_buy,l_amount,net_amount,net_rate,amount_rate,float_values,reason')

# 6. margin_detail 两融明细
update_market_table('margin_detail', 'margin_detail',
    ['ts_code','trade_date','rzye','rqye','rzmre','rqyl','rzche','rqchl'],
    'ts_code,trade_date,rzye,rqye,rzmre,rqyl,rzche,rqchl')

# ── ggt_daily 港股通（无ts_code，特殊处理）──────────────────────
cur.execute("SELECT MAX(trade_date) FROM ggt_daily")
last = cur.fetchone()[0] or '20260101'
try:
    _, items = call('ggt_daily', {'start_date': last, 'end_date': TODAY},
        'trade_date,buy_amount,buy_volume,sell_amount,sell_volume')
    n = 0
    for r in items:
        if r[0] > last:
            try:
                conn.execute("INSERT OR REPLACE INTO ggt_daily VALUES(?,?,?,?,?)",
                    [r[0], float(r[1] or 0), float(r[2] or 0), float(r[3] or 0), float(r[4] or 0)])
                n += 1
            except: pass
    conn.commit()
    cur.execute("SELECT MAX(trade_date) FROM ggt_daily")
    log(f"  ✅ ggt_daily: +{n}行，最新={cur.fetchone()[0]}")
except Exception as e:
    log(f"  ggt_daily 失败: {e}")

# ── margin 两融汇总（三交易所）────────────────────────────────
cur.execute("SELECT MAX(trade_date) FROM margin")
last = cur.fetchone()[0] or '20260101'
dates = get_trade_dates(last, TODAY)
if dates:
    total = 0
    for exch in ['SSE', 'SZSE', 'BSE']:
        for date in dates:
            try:
                _, items = call('margin', {'trade_date': date, 'exchange_id': exch},
                    'trade_date,exchange_id,rzye,rzmre,rzche,rqye,rqmcl,rzrqye,rqyl')
                for r in items:
                    try:
                        conn.execute("""INSERT OR REPLACE INTO margin
                            (trade_date,exchange_id,rzye,rzmre,rzche,rqye,rqmcl,rzrqye,rqyl)
                            VALUES(?,?,?,?,?,?,?,?,?)""",
                            [r[0], r[1]] + [float(x or 0) if x else None for x in r[2:9]])
                        total += 1
                    except: pass
            except Exception as e:
                pass
            time.sleep(0.3)
    conn.commit()
    cur.execute("SELECT MAX(trade_date) FROM margin")
    log(f"  ✅ margin: +{total}行，最新={cur.fetchone()[0]}")
else:
    log(f"  margin: 已最新（{last}）")

conn.close()
log("======== 日更完成 ========")

# 写日志文件
logpath = f"/Users/ziruzhu/stock-tools/logs/daily_update_{datetime.now().strftime('%Y%m%d')}.log"
import os
os.makedirs('/Users/ziruzhu/stock-tools/logs', exist_ok=True)
with open(logpath, 'w', encoding='utf-8') as f:
    f.write('\n'.join(LOG))
