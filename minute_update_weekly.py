#!/usr/bin/env python3
"""
stock_all.db 分钟线周度增量更新
每周六晚运行，补全 stk_5min / stk_15min / stk_30min / stk_60min
- 增量：自动检测每张表最新日期，只补缺口
- 断点续跑：checkpoint 文件记录进度，中断后从上次位置继续
- 全市场：从 stock_list 取所有股票（排除北交所.BJ）
"""
import requests, sqlite3, time, os, json
from datetime import datetime, timedelta

TOKEN = '2b6b1b830a45468b9856e6500ce40a90'
BASE  = 'https://ts.gyzcloud.top/api'
DB    = '/Users/ziruzhu/stock-data/stock_all.db'
LOG_DIR = '/Users/ziruzhu/stock-tools/logs'
CKPT_FILE = '/Users/ziruzhu/stock-tools/minute_update_ckpt.json'

os.makedirs(LOG_DIR, exist_ok=True)

LOG = []
def log(m):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {m}"
    print(line, flush=True)
    LOG.append(line)

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
                log(f"  频控，等{wait}秒...")
                time.sleep(wait)
            else:
                raise Exception(f"API错误: {msg}")
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(5)
    raise Exception("重试失败")

# checkpoint 读写
def load_ckpt():
    if os.path.exists(CKPT_FILE):
        with open(CKPT_FILE) as f:
            return json.load(f)
    return {}

def save_ckpt(ckpt):
    with open(CKPT_FILE, 'w') as f:
        json.dump(ckpt, f)

conn = sqlite3.connect(DB)
conn.execute('PRAGMA journal_mode=WAL')
cur = conn.cursor()

log(f"======== 分钟线周更 {datetime.now().strftime('%Y-%m-%d %H:%M')} ========")

# 获取全市场股票（排除北交所）
cur.execute("SELECT ts_code FROM stock_list WHERE ts_code NOT LIKE '%.BJ' ORDER BY ts_code")
all_codes = [r[0] for r in cur.fetchall()]
log(f"  全市场股票数: {len(all_codes)} 只（已排除北交所）")

TODAY_STR = datetime.now().strftime('%Y-%m-%d')
ckpt = load_ckpt()

# 四个频率配置
FREQS = [
    {'freq': '5min',  'table': 'stk_5min',
     'cols': ['ts_code','trade_date','trade_time','open','high','low','close','vol','amount'],
     'has_trade_date': True},
    {'freq': '15min', 'table': 'stk_15min',
     'cols': ['ts_code','trade_date','trade_time','open','high','low','close','vol','amount'],
     'has_trade_date': True},
    {'freq': '30min', 'table': 'stk_30min',
     'cols': ['ts_code','trade_time','open','close','high','low','vol','amount'],
     'has_trade_date': False},
    {'freq': '60min', 'table': 'stk_60min',
     'cols': ['ts_code','trade_time','open','close','high','low','vol','amount'],
     'has_trade_date': False},
]

for fc in FREQS:
    freq  = fc['freq']
    table = fc['table']
    cols  = fc['cols']
    has_td = fc['has_trade_date']

    log(f"\n=== {table} ({freq}) ===")

    # 找该表最新时间
    if has_td:
        cur.execute(f"SELECT MAX(trade_date) FROM {table}")
        last_val = cur.fetchone()[0]
        last_date = last_val if last_val else '2026-06-01'
        # last_date 格式 YYYY-MM-DD
        start_dt = datetime.strptime(last_date, '%Y-%m-%d') + timedelta(days=1)
    else:
        cur.execute(f"SELECT MAX(trade_time) FROM {table}")
        last_val = cur.fetchone()[0]
        if last_val:
            last_date = last_val[:10]
            start_dt = datetime.strptime(last_date, '%Y-%m-%d') + timedelta(days=1)
        else:
            start_dt = datetime.now() - timedelta(days=30)

    end_dt = datetime.now()
    # 如果已是最新，跳过
    if start_dt.date() >= end_dt.date():
        log(f"  已最新（{last_date}），跳过")
        continue

    start_str = start_dt.strftime('%Y-%m-%d') + ' 09:00:00'
    end_str   = end_dt.strftime('%Y-%m-%d')   + ' 15:30:00'
    log(f"  补数据范围: {start_str[:10]} ~ {end_str[:10]}")

    # 从 checkpoint 恢复
    ckpt_key = f"{table}_last_code"
    resume_code = ckpt.get(ckpt_key, '')
    resuming = bool(resume_code)

    total_new = 0
    fail_count = 0
    codes_done = 0

    placeholders = ','.join('?' * len(cols))
    col_str = ','.join(cols)

    for code in all_codes:
        # 断点续跑：跳过已处理的
        if resuming:
            if code == resume_code:
                resuming = False  # 从这里开始处理
            continue

        try:
            _, items = call_ts('stk_mins',
                {'ts_code': code, 'freq': freq,
                 'start_date': start_str, 'end_date': end_str},
                'ts_code,trade_time,open,high,low,close,vol,amount')

            if items:
                n = 0
                for r in items:
                    # r = [ts_code, trade_time, open, high, low, close, vol, amount]
                    ts_code    = r[0]
                    trade_time = r[1]  # '2026-07-18 09:30:00'
                    trade_date = trade_time[:10] if trade_time else None

                    if has_td:
                        # stk_5min/15min: ts_code, trade_date, trade_time, open, high, low, close, vol, amount
                        vals = [ts_code, trade_date, trade_time,
                                float(r[2] or 0), float(r[3] or 0), float(r[4] or 0),
                                float(r[5] or 0), float(r[6] or 0), float(r[7] or 0)]
                    else:
                        # stk_30min/60min: ts_code, trade_time, open, close, high, low, vol, amount
                        vals = [ts_code, trade_time,
                                float(r[2] or 0), float(r[5] or 0),  # open, close
                                float(r[3] or 0), float(r[4] or 0),  # high, low
                                float(r[6] or 0), float(r[7] or 0)]  # vol, amount
                    try:
                        conn.execute(f"INSERT OR REPLACE INTO {table} ({col_str}) VALUES({placeholders})", vals)
                        n += 1
                    except: pass
                total_new += n

            codes_done += 1
            fail_count = 0

            # 每100只提交一次，并保存 checkpoint
            if codes_done % 100 == 0:
                conn.commit()
                ckpt[ckpt_key] = code
                save_ckpt(ckpt)
                log(f"  进度: {codes_done}/{len(all_codes)} 只，新增{total_new}行")

        except Exception as e:
            fail_count += 1
            if fail_count >= 5:
                log(f"  连续5次失败，暂停此频率。上次处理到: {code}")
                ckpt[ckpt_key] = code
                save_ckpt(ckpt)
                break
            time.sleep(1)

        time.sleep(0.15)  # 频控间隔

    conn.commit()
    # 该频率完成，清除 checkpoint
    ckpt.pop(ckpt_key, None)
    save_ckpt(ckpt)
    log(f"  ✅ {table}: 共新增{total_new}行")

conn.close()
log("======== 分钟线周更完成 ========")

# 写日志
log_path = os.path.join(LOG_DIR, f"minute_update_{datetime.now().strftime('%Y%m%d')}.log")
with open(log_path, 'w', encoding='utf-8') as f:
    f.write('\n'.join(LOG))
log(f"日志: {log_path}")
