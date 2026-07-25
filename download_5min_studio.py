#!/usr/bin/env python3
"""
5/15分钟线下载 — Studio版
范围: 近50根K线，A股全部
写入 Studio 的 stock_all.db，之后同步回 Air
"""
import requests, sqlite3, time, os

DB = os.path.expanduser('~/stock-data/stock_all.db')

conn = sqlite3.connect(DB)
cur = conn.cursor()

# 确保表存在
cur.execute("""CREATE TABLE IF NOT EXISTS stk_5min (
    ts_code TEXT, trade_date TEXT, trade_time TEXT,
    open REAL, high REAL, low REAL, close REAL, vol REAL, amount REAL
)""")
cur.execute("""CREATE TABLE IF NOT EXISTS stk_15min (
    ts_code TEXT, trade_date TEXT, trade_time TEXT,
    open REAL, high REAL, low REAL, close REAL, vol REAL, amount REAL
)""")
conn.commit()

# 所有A股
cur.execute("SELECT DISTINCT ts_code FROM daily")
stocks = [r[0] for r in cur.fetchall()]
print(f"股票总数: {len(stocks)}")

for freq, tbl in [('5', 'stk_5min'), ('15', 'stk_15min')]:
    # 清空旧数据重新下载
    cur.execute(f"DELETE FROM {tbl}")
    conn.commit()
    print(f'\n{freq}分钟线: 开始({len(stocks)}只)', flush=True)
    total = 0
    for i, ts in enumerate(stocks):
        code = ts.split('.')[0]
        mkt = '1' if ts.endswith('.SH') else '0'
        try:
            url = f'https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={mkt}.{code}&klt={freq}&fqt=1&end=20260709&lmt=50&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57'
            r = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            klines = r.json().get('data', {}).get('klines', [])
            if not klines:
                time.sleep(0.05)
                continue
            n = 0
            for k in klines:
                p = k.split(',')
                if len(p) >= 7:
                    try:
                        cur.execute(f"INSERT OR REPLACE INTO {tbl} VALUES(?,?,?,?,?,?,?,?)",
                            (ts, p[0], p[1], p[2], p[3], p[4], p[5], p[6]))
                        n += 1
                    except:
                        pass
            total += n
            if (i + 1) % 200 == 0:
                conn.commit()
                pct = (i + 1) / len(stocks) * 100
                print(f'  {i+1}/{len(stocks)} ({pct:.0f}%) 已写{total}条', flush=True)
        except:
            pass
        time.sleep(0.3)
    conn.commit()
    print(f'  {freq}分钟线完成: {total}条', flush=True)

conn.close()
print('\n✅ 全完成')
