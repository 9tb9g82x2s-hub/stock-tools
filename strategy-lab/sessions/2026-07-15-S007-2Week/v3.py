#!/usr/bin/env python3
"""S007 v3: 强势回踩策略 + 市值<500亿过滤 — Studio版"""
import sqlite3, pandas as pd, numpy as np, os, time, akshare as ak

t0 = time.time()
DB = os.path.expanduser('~/stock-data/stock_all.db')
OUT = os.path.expanduser('~/stock-tools/strategy-lab/sessions/2026-07-15-S007-2Week/')

conn = sqlite3.connect(DB); cur = conn.cursor()
cur.execute("SELECT DISTINCT ts_code FROM stock_list WHERE name NOT LIKE '%ST%' AND name NOT LIKE '%*ST%' AND name NOT LIKE '%退%'")
clean = set(r[0] for r in cur.fetchall())
cur.execute("""SELECT ts_code FROM daily WHERE trade_date>='20240101' AND trade_date<'20260101'
    GROUP BY ts_code ORDER BY AVG(CAST(vol AS REAL)*CAST(close AS REAL)) DESC LIMIT 1000""")
pool = [r[0] for r in cur.fetchall() if r[0] in clean]

# 用成交额排名近似市值: 排名200-1000 ≈ 中盘股(<500亿)
# 大市值股票成交额最高，取后80%近似
pool_filtered = pool[int(len(pool)*0.2):]  # 去掉前20%大市值
print(f"原始池: {len(pool)}  → 中盘股: {len(pool_filtered)}只")

cs = ','.join(f"'{c}'" for c in pool_filtered)
daily_all = pd.read_sql(f"""SELECT ts_code, trade_date,
    CAST(open AS REAL) as o, CAST(close AS REAL) as c,
    CAST(high AS REAL) as h, CAST(low AS REAL) as l, CAST(vol AS REAL) as v
    FROM daily WHERE ts_code IN ({cs}) AND trade_date>='20210101'
    ORDER BY ts_code, trade_date""", conn)
daily_all['trade_date'] = pd.to_datetime(daily_all['trade_date'], format='%Y%m%d')
conn.close()

idx = ak.stock_zh_index_daily(symbol='sh000300')
idx['date'] = pd.to_datetime(idx['date']); idx['ma200'] = idx['close'].rolling(200).mean()
bull_set = set(idx[idx['close']>idx['ma200']]['date'])

HOLD = 10
results = []

for tc in pool_filtered:
    sd = daily_all[daily_all['ts_code']==tc].sort_values('trade_date').reset_index(drop=True)
    if len(sd) < 260: continue
    c = sd['c'].values; v = sd['v'].values; dates = list(sd['trade_date'])
    
    ret_10d = c / pd.Series(c).shift(10).values - 1
    
    gain = np.where(np.diff(c, prepend=c[0])>0, np.diff(c, prepend=c[0]), 0)
    loss = np.where(np.diff(c, prepend=c[0])<0, -np.diff(c, prepend=c[0]), 0)
    rsi = 100 - 100 / (1 + pd.Series(gain).rolling(14).mean().values / 
                       np.maximum(pd.Series(loss).rolling(14).mean().values, 1e-10))
    
    ma10 = pd.Series(c).rolling(10).mean().values
    ma20 = pd.Series(c).rolling(20).mean().values
    vma20 = pd.Series(v).rolling(20).mean().values
    
    for i in range(259, len(c)):
        if i + HOLD >= len(c): continue
        if dates[i] not in bull_set: continue
        entry = c[i]
        
        # 强势回踩: 10天涨>15% + 回踩MA10 ±3% + 缩量 + RSI>40
        if np.isnan(ret_10d[i]) or ret_10d[i] <= 0.15: continue
        if np.isnan(ma10[i]): continue
        near_ma = abs(c[i] / ma10[i] - 1) < 0.03
        if not near_ma: continue
        if v[i] > vma20[i]: continue  # 缩量
        if np.isnan(rsi[i]) or rsi[i] <= 40: continue
        
        prices = [float(c[i+j]) for j in range(HOLD+1)]
        base_r = (prices[-1] / entry - 1) * 100
        
        tp_r = base_r; tp_h = False; sl_h = False; ex_d = HOLD
        for j in range(1, len(prices)):
            r = (prices[j] / entry - 1) * 100
            if r >= 15: tp_r = 15; ex_d = j; tp_h = True; break
            if r <= -8: tp_r = -8; ex_d = j; sl_h = True; break
        
        cap_val = 0
        results.append({
            'strategy': '强势回踩', 'ts_code': tc, 'date': str(dates[i]),
            'return': base_r, 'tp_return': tp_r, 'tp_hit': tp_h, 'sl_hit': sl_h,
            'exit_day': ex_d, 'market_cap': cap_val,
            'rsi': rsi[i], 'vol_ratio': v[i]/max(vma20[i],1),
        })

df_r = pd.DataFrame(results)
print(f"\n信号: {len(df_r)}笔")

br = df_r['return']; pr = df_r['tp_return']
n = len(df_r)
print(f'  持有到期: 中位{np.median(br):+.2f}%  胜率{(br>0).mean()*100:.0f}%  均{br.mean():+.2f}%')
print(f'  止盈+15%/止损-8%: 中位{np.median(pr):+.2f}%  胜率{(pr>0).mean()*100:.0f}%  均{pr.mean():+.2f}%')
print(f'  止盈: {df_r["tp_hit"].sum()}({df_r["tp_hit"].mean()*100:.0f}%)  止损: {df_r["sl_hit"].sum()}({df_r["sl_hit"].mean()*100:.0f}%)')
print(f'  赚>10%: {(br>10).mean()*100:.0f}%  赚>15%: {(br>15).mean()*100:.0f}%')
print(f'  亏>8%: {(br<-8).mean()*100:.0f}%')

# 年度
df_r['year'] = pd.to_datetime(df_r['date']).dt.year
for y in [2022,2023,2024,2025]:
    sub = df_r[df_r['year']==y]
    if len(sub) < 3: continue
    print(f'  {y}: {len(sub)}笔  中位{np.median(sub["return"]):+.2f}%  胜率{(sub["return"]>0).mean()*100:.0f}%')

df_r.to_csv(f'{OUT}signals_v3.csv', index=False)
print(f'\n耗时: {time.time()-t0:.0f}s')
PYEOF