#!/usr/bin/env python3
"""S007 v2: 涨停板追涨/缺口突破/动量加速 — Studio版"""
import sqlite3, pandas as pd, numpy as np, os, time

t0 = time.time()
DB = os.path.expanduser('~/stock-data/stock_all.db')
OUT = os.path.expanduser('~/stock-tools/strategy-lab/sessions/2026-07-15-S007-2Week/')

os.makedirs(OUT, exist_ok=True)

conn = sqlite3.connect(DB); cur = conn.cursor()
cur.execute("SELECT DISTINCT ts_code FROM stock_list WHERE name NOT LIKE '%ST%' AND name NOT LIKE '%*ST%' AND name NOT LIKE '%退%'")
clean = set(r[0] for r in cur.fetchall())
cur.execute("""SELECT ts_code FROM daily WHERE trade_date>='20240101' AND trade_date<'20260101'
    GROUP BY ts_code ORDER BY AVG(CAST(vol AS REAL)*CAST(close AS REAL)) DESC LIMIT 1000""")
pool = [r[0] for r in cur.fetchall() if r[0] in clean]
cs = ','.join(f"'{c}'" for c in pool)

daily_all = pd.read_sql(f"""SELECT ts_code, trade_date,
    CAST(open AS REAL) as o, CAST(close AS REAL) as c,
    CAST(high AS REAL) as h, CAST(low AS REAL) as l, CAST(vol AS REAL) as v
    FROM daily WHERE ts_code IN ({cs}) AND trade_date>='20210101'
    ORDER BY ts_code, trade_date""", conn)
daily_all['trade_date'] = pd.to_datetime(daily_all['trade_date'], format='%Y%m%d')
conn.close()

import akshare as ak
idx = ak.stock_zh_index_daily(symbol='sh000300')
idx['date'] = pd.to_datetime(idx['date']); idx['ma200'] = idx['close'].rolling(200).mean()
bull_set = set(idx[idx['close']>idx['ma200']]['date'])

HOLD = 10
results = {}

for tc in pool:
    sd = daily_all[daily_all['ts_code']==tc].sort_values('trade_date').reset_index(drop=True)
    if len(sd) < 260: continue
    c = sd['c'].values; o = sd['o'].values; h = sd['h'].values; l = sd['l'].values
    v = sd['v'].values; dates = list(sd['trade_date'])
    
    ret_1d = np.diff(c, prepend=c[0]) / np.maximum(np.abs(c), 1e-10) * 100
    ret_5d = c / pd.Series(c).shift(5).values - 1
    ret_10d = c / pd.Series(c).shift(10).values - 1
    
    gain = np.where(np.diff(c, prepend=c[0])>0, np.diff(c, prepend=c[0]), 0)
    loss = np.where(np.diff(c, prepend=c[0])<0, -np.diff(c, prepend=c[0]), 0)
    rsi = 100 - 100 / (1 + pd.Series(gain).rolling(14).mean().values / 
                       np.maximum(pd.Series(loss).rolling(14).mean().values, 1e-10))
    
    ma5 = pd.Series(c).rolling(5).mean().values
    ma10 = pd.Series(c).rolling(10).mean().values
    ma20 = pd.Series(c).rolling(20).mean().values
    ma60 = pd.Series(c).rolling(60).mean().values
    vma20 = pd.Series(v).rolling(20).mean().values
    high_20 = pd.Series(h).rolling(20).max().values
    
    for i in range(259, len(c)):
        if i + HOLD >= len(c): continue
        if dates[i] not in bull_set: continue
        entry = c[i]
        prices = [float(c[i+j]) for j in range(HOLD+1)]
        
        # === 策略A: 涨停板次日追涨 ===
        # 昨日涨停(>9.5%)，今日开盘不跳太低，追入
        if i >= 1 and ret_1d[i-1] > 9.5:
            # 开盘回撤<3%
            if (o[i] / c[i-1] - 1) * 100 > -3:
                base_r = (prices[-1] / entry - 1) * 100
                tp_r = base_r; tp_h = False; sl_h = False; ex_d = HOLD
                for j in range(1, len(prices)):
                    r = (prices[j] / entry - 1) * 100
                    if r >= 10: tp_r = 10; ex_d = j; tp_h = True; break
                    if r <= -5: tp_r = -5; ex_d = j; sl_h = True; break
                results.setdefault('涨停追涨', []).append(
                    (base_r, tp_r, tp_h, sl_h, ex_d, rsi[i], v[i]/max(vma20[i],1)))
        
        # === 策略B: 放量突破20日新高 ===
        if c[i] >= high_20[i-1] * 0.998 and v[i] > vma20[i] * 2.5:
            if not np.isnan(rsi[i]) and 50 < rsi[i] < 85:
                base_r = (prices[-1] / entry - 1) * 100
                tp_r = base_r; tp_h = False; sl_h = False; ex_d = HOLD
                for j in range(1, len(prices)):
                    r = (prices[j] / entry - 1) * 100
                    if r >= 10: tp_r = 10; ex_d = j; tp_h = True; break
                    if r <= -5: tp_r = -5; ex_d = j; sl_h = True; break
                results.setdefault('放量新高', []).append(
                    (base_r, tp_r, tp_h, sl_h, ex_d, rsi[i], v[i]/max(vma20[i],1)))
        
        # === 策略C: 强势股5日回踩MA10 ===
        if i >= 5 and ret_10d[i] > 0.15:  # 10天涨>15%（强势股）
            if c[i] > ma10[i] * 0.97 and c[i] < ma10[i] * 1.03:  # 回踩MA10附近
                if v[i] < vma20[i]:  # 缩量
                    if not np.isnan(rsi[i]) and rsi[i] > 40:
                        base_r = (prices[-1] / entry - 1) * 100
                        tp_r = base_r; tp_h = False; sl_h = False; ex_d = HOLD
                        for j in range(1, len(prices)):
                            r = (prices[j] / entry - 1) * 100
                            if r >= 10: tp_r = 10; ex_d = j; tp_h = True; break
                            if r <= -5: tp_r = -5; ex_d = j; sl_h = True; break
                        results.setdefault('强势回踩', []).append(
                            (base_r, tp_r, tp_h, sl_h, ex_d, rsi[i], v[i]/max(vma20[i],1)))
        
        # === 策略D: 缺口不补 ===
        # 今日跳空高开>2%且不回补（最低>昨收）
        if i >= 1:
            gap = (o[i] / c[i-1] - 1) * 100
            if gap > 2 and gap < 8:  # 跳空2-8%
                if l[i] > c[i-1]:  # 最低不破昨收 = 缺口未补
                    if v[i] > vma20[i] * 1.2:  # 放量确认
                        if not np.isnan(rsi[i]) and rsi[i] < 80:
                            base_r = (prices[-1] / entry - 1) * 100
                            tp_r = base_r; tp_h = False; sl_h = False; ex_d = HOLD
                            for j in range(1, len(prices)):
                                r = (prices[j] / entry - 1) * 100
                                if r >= 10: tp_r = 10; ex_d = j; tp_h = True; break
                                if r <= -5: tp_r = -5; ex_d = j; sl_h = True; break
                            results.setdefault('缺口不补', []).append(
                                (base_r, tp_r, tp_h, sl_h, ex_d, rsi[i], v[i]/max(vma20[i],1)))

# 输出
for sname in ['涨停追涨', '放量新高', '强势回踩', '缺口不补']:
    if sname not in results: continue
    data = results[sname]
    br = np.array([d[0] for d in data])
    pr = np.array([d[1] for d in data])
    tp_hits = sum(1 for d in data if d[2])
    sl_hits = sum(1 for d in data if d[3])
    n = len(data)
    print(f'\n=== {sname}: {n}笔 ===')
    print(f'  持有到期: 中位{np.median(br):+.2f}%  胜率{(br>0).mean()*100:.0f}%  均{br.mean():+.2f}%')
    print(f'  止盈+10%/止损-5%: 中位{np.median(pr):+.2f}%  胜率{(pr>0).mean()*100:.0f}%  均{pr.mean():+.2f}%')
    print(f'  触及止盈: {tp_hits}笔({tp_hits/n*100:.0f}%)  止损: {sl_hits}笔({sl_hits/n*100:.0f}%)')
    print(f'  赚>10%: {(br>10).mean()*100:.0f}%  亏>5%: {(br<-5).mean()*100:.0f}%')

print(f'\n总耗时: {time.time()-t0:.0f}s')
PYEOF