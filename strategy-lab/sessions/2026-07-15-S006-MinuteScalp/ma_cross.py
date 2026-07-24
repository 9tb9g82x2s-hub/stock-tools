#!/usr/bin/env python3
"""S006: 纯MA金叉死叉分钟线策略 — Studio版"""
import sqlite3, pandas as pd, numpy as np, os, time

t0 = time.time()
DB = os.path.expanduser('~/stock-data/stock_all.db')
OUT = os.path.expanduser('~/stock-tools/strategy-lab/sessions/2026-07-15-S006-MinuteScalp/ma_results.csv')

print(f"MA Cross Studio run started at {time.strftime('%H:%M:%S')}")

# Top 1000 stocks
conn = sqlite3.connect(DB); cur = conn.cursor()
cur.execute("SELECT DISTINCT ts_code FROM stock_list WHERE name NOT LIKE '%ST%' AND name NOT LIKE '%*ST%' AND name NOT LIKE '%退%'")
clean = set(r[0] for r in cur.fetchall())
cur.execute("""SELECT ts_code FROM daily WHERE trade_date>='20240101' AND trade_date<'20260101' 
    GROUP BY ts_code ORDER BY AVG(CAST(vol AS REAL)*CAST(close AS REAL)) DESC LIMIT 1000""")
pool = [r[0] for r in cur.fetchall() if r[0] in clean]
conn.close()
print(f"池: {len(pool)}只")

# 策略: MA5上穿MA10买入, MA5下穿MA20卖出
# 分钟线参数映射 >> 日线MA5=5天, MA10=10天, MA20=20天

configs = [
    ('60min', 'stk_60min', 4),
    ('30min', 'stk_30min', 8),
]

all_trades = []

for freq, tbl, bpd in configs:
    MA_FAST = 5 * bpd    # MA5
    MA_SLOW_ENTRY = 10 * bpd  # MA10 (入场金叉)
    MA_SLOW_EXIT = 20 * bpd   # MA20 (出场死叉)
    NEED = max(MA_SLOW_EXIT + 100, 260)
    
    batch_size = 50
    cfg_trades = 0
    
    print(f'\n{"="*60}')
    print(f'  {freq}: MA5×{MA_FAST//bpd}天 金叉MA{MA_SLOW_ENTRY//bpd}天入场 / 死叉MA{MA_SLOW_EXIT//bpd}天出场')
    
    for batch_start in range(0, len(pool), batch_size):
        batch_codes = pool[batch_start:batch_start+batch_size]
        cs = ','.join(f"'{c}'" for c in batch_codes)
        
        conn = sqlite3.connect(DB)
        df = pd.read_sql(f"""
            SELECT ts_code, trade_time, CAST(close AS REAL) as c
            FROM {tbl} WHERE ts_code IN ({cs}) AND trade_time>='20240101'
            ORDER BY ts_code, trade_time
        """, conn)
        conn.close()
        
        if len(df) == 0: continue
        df['trade_time'] = pd.to_datetime(df['trade_time'])
        
        for tc in batch_codes:
            sd = df[df['ts_code']==tc].sort_values('trade_time').reset_index(drop=True)
            if len(sd) < NEED: continue
            c = sd['c'].values; dates = list(sd['trade_time'])
            
            ma_f = pd.Series(c).rolling(MA_FAST).mean().values
            ma_s = pd.Series(c).rolling(MA_SLOW_ENTRY).mean().values
            ma_x = pd.Series(c).rolling(MA_SLOW_EXIT).mean().values
            
            in_position = False
            entry_price = 0
            entry_idx = 0
            
            for i in range(NEED, len(c)):
                if np.isnan(ma_f[i]) or np.isnan(ma_s[i]) or np.isnan(ma_x[i]): continue
                if i < 1: continue
                if np.isnan(ma_f[i-1]) or np.isnan(ma_s[i-1]) or np.isnan(ma_x[i-1]): continue
                
                if not in_position:
                    # 入场: MA5上穿MA10
                    if ma_f[i] > ma_s[i] and ma_f[i-1] <= ma_s[i-1]:
                        in_position = True
                        entry_price = c[i]
                        entry_idx = i
                else:
                    # 出场: MA5下穿MA20
                    exit_signal = ma_f[i] < ma_x[i] and ma_f[i-1] >= ma_x[i-1]
                    if exit_signal:
                        ret_pct = (c[i] / entry_price - 1) * 100
                        hold_bars = i - entry_idx
                        hold_days = hold_bars / bpd
                        cfg_trades += 1
                        all_trades.append({
                            'freq': freq, 'ts_code': tc,
                            'entry_date': str(dates[entry_idx]),
                            'exit_date': str(dates[i]),
                            'entry_price': entry_price, 'exit_price': c[i],
                            'return_pct': ret_pct, 'hold_bars': hold_bars,
                            'hold_days': hold_days,
                        })
                        in_position = False
        
        if (batch_start // batch_size) % 5 == 0:
            print(f"  [{freq}] batch {batch_start//batch_size+1}: {cfg_trades} trades, {time.time()-t0:.0f}s")
    
    # 统计
    trades_df = pd.DataFrame([t for t in all_trades if t['freq']==freq])
    if len(trades_df) > 0:
        r = trades_df['return_pct']
        print(f'\n  [{freq}] 总交易: {len(trades_df)}笔')
        print(f'    胜率: {(r>0).mean()*100:.1f}%')
        print(f'    中位: {np.median(r):+.2f}%  均: {r.mean():+.2f}%')
        print(f'    赚>5%: {(r>5).mean()*100:.0f}%  亏>5%: {(r<-5).mean()*100:.0f}%')
        print(f'    平均持有: {trades_df["hold_days"].mean():.1f}天')
        print(f'    最大: {r.max():+.1f}%  最小: {r.min():+.1f}%')

# 保存
os.makedirs(os.path.dirname(OUT), exist_ok=True)
df_out = pd.DataFrame(all_trades)
df_out.to_csv(OUT, index=False)
print(f'\n{"="*60}')
print(f'完成！{len(df_out)}笔, {time.time()-t0:.0f}s')
print(f'输出: {OUT}')
