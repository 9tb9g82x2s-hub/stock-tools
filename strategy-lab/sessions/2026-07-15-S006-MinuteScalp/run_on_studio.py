#!/usr/bin/env python3
"""S006: S004分钟线癫痫策略 — Studio全市场版"""
import sqlite3, pandas as pd, numpy as np, os, time, sys

t0 = time.time()
DB = os.path.expanduser('~/stock-data/stock_all.db')
OUT = os.path.expanduser('~/stock-tools/strategy-lab/sessions/2026-07-15-S006-MinuteScalp/results.csv')

print(f"S006 Studio run started at {time.strftime('%H:%M:%S')}")
print(f"Output: {OUT}")

# Step 1: 取top N股票
conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.execute("SELECT DISTINCT ts_code FROM stock_list WHERE name NOT LIKE '%ST%' AND name NOT LIKE '%*ST%' AND name NOT LIKE '%退%'")
clean = set(r[0] for r in cur.fetchall())

TOP = 1000
cur.execute(f"""
    SELECT ts_code FROM daily 
    WHERE trade_date>='20240101' AND trade_date<'20260101' 
    GROUP BY ts_code 
    ORDER BY AVG(CAST(vol AS REAL)*CAST(close AS REAL)) DESC 
    LIMIT {TOP}
""")
pool = [r[0] for r in cur.fetchall() if r[0] in clean]
conn.close()
print(f"Top {TOP} 流动性股票: {len(pool)} 只")

# Step 2: 定义参数组合
configs = [
    # (name, table, bars_per_day, ma_days, angle_thr, std_bars, obv_ratio, macd_ratio)
    ('60min_A', 'stk_60min', 4, 20, 0.05, 40, 0.70, 0.60),
    ('60min_B', 'stk_60min', 4, 10, 0.03, 20, 0.70, 0.60),
    ('30min_A', 'stk_30min', 8, 20, 0.05, 80, 0.70, 0.60),
    ('30min_B', 'stk_30min', 8, 10, 0.03, 40, 0.70, 0.60),
]

all_signals = []

for cfg_name, tbl, bpd, ma_days, ang_thr, std_bars, obv_ratio, macd_ratio in configs:
    print(f"\n{'='*60}")
    print(f"  {cfg_name}: {tbl}  MA{ma_days}天  angle>{ang_thr}  std{std_bars//bpd}天")
    
    MA_OBV = ma_days * bpd
    MACD_W = 30 * bpd
    MA_S = 5 * bpd
    MA_M = 10 * bpd
    ACCEL = 5 * bpd
    NEED = max(MA_OBV + 200, 260)
    HOLD_BARS = [int(2.5*bpd), int(5*bpd), int(7.5*bpd), int(10*bpd), int(15*bpd)]
    
    # 分批加载数据（每50只一批）
    batch_size = 50
    cfg_signals = 0
    cfg_rets = []
    
    for batch_start in range(0, len(pool), batch_size):
        batch_codes = pool[batch_start:batch_start+batch_size]
        cs = ','.join(f"'{c}'" for c in batch_codes)
        
        conn = sqlite3.connect(DB)
        df = pd.read_sql(f"""
            SELECT ts_code, trade_time,
                   CAST(close AS REAL) as c, CAST(vol AS REAL) as v,
                   CAST(high AS REAL) as h, CAST(low AS REAL) as l
            FROM {tbl}
            WHERE ts_code IN ({cs}) AND trade_time>='20240101'
            ORDER BY ts_code, trade_time
        """, conn)
        conn.close()
        
        if len(df) == 0: continue
        df['trade_time'] = pd.to_datetime(df['trade_time'])
        
        for tc in batch_codes:
            sd = df[df['ts_code']==tc].sort_values('trade_time').reset_index(drop=True)
            if len(sd) < NEED: continue
            c = sd['c'].values; v = sd['v'].values; h = sd['h'].values; l_arr = sd['l'].values
            dates = list(sd['trade_time'])
            
            diff_s = np.diff(c, prepend=c[0])
            sgn = np.where(diff_s>0, 1, np.where(diff_s<0, -1, 0))
            obv = np.cumsum(sgn * v)
            mb = pd.Series(obv).rolling(MA_OBV).mean().values
            e12 = pd.Series(c).ewm(span=12, adjust=False).mean().values
            e26 = pd.Series(c).ewm(span=26, adjust=False).mean().values
            dif = e12 - e26
            dea = pd.Series(dif).ewm(span=9, adjust=False).mean().values
            br = 2 * (dif - dea)
            ma_s = pd.Series(c).rolling(MA_S).mean().values
            ma_m = pd.Series(c).rolling(MA_M).mean().values
            oa = pd.Series((obv>mb).astype(float)).rolling(MA_OBV, min_periods=1).sum().values
            os_std = pd.Series(obv).rolling(std_bars, min_periods=1).std().values
            md = pd.Series(((dif>0)&(dea>0)).astype(float)).rolling(MACD_W, min_periods=1).sum().values
            
            for i in range(NEED, len(c)):
                vw = min(MA_OBV, i+1)
                if oa[i] < vw * obv_ratio: continue
                std_v = os_std[i]
                if np.isnan(std_v) or std_v < 1: continue
                o5 = obv[max(0, i-ACCEL+1):i+1]
                m5_ = mb[max(0, i-ACCEL+1):i+1]
                if len(o5) < 3 or np.any(np.isnan(o5)) or np.any(np.isnan(m5_)): continue
                sd_s, _ = np.polyfit(np.arange(len(o5)), o5-m5_, 1)[:2]
                if sd_s/std_v <= ang_thr: continue
                vmacd = min(MACD_W, i+1)
                if md[i] < vmacd * macd_ratio: continue
                if np.isnan(ma_s[i]) or np.isnan(ma_m[i]): continue
                if np.isnan(ma_s[i-1]) or np.isnan(ma_m[i-1]): continue
                if not(ma_s[i] > ma_m[i] and ma_s[i-1] <= ma_m[i-1]): continue
                if br[i] <= 0: continue
                
                cfg_signals += 1
                cfg_rets.append({
                    'config': cfg_name, 'ts_code': tc,
                    'date': dates[i], 'entry_price': c[i]
                })
                
                for hold in HOLD_BARS:
                    if i + hold >= len(c): continue
                    ret = (c[i+hold]/c[i] - 1) * 100
                    cfg_rets.append({
                        'config': cfg_name, 'ts_code': tc,
                        'date': dates[i], 'hold_days': hold/bpd, 'return': ret
                    })
        
        if (batch_start // batch_size) % 4 == 0:
            elapsed = time.time() - t0
            print(f"  [{cfg_name}] batch {batch_start//batch_size+1}: {cfg_signals} signals, {elapsed:.0f}s")
    
    # 汇总
    rets_df = pd.DataFrame([r for r in cfg_rets if 'return' in r])
    print(f"\n  [{cfg_name}] 总信号: {cfg_signals}, 收益记录: {len(rets_df)}")
    if len(rets_df) > 0:
        for hd in sorted(rets_df['hold_days'].unique()):
            sub = rets_df[rets_df['hold_days']==hd]
            if len(sub) < 10: continue
            r = sub['return']
            print(f"    {hd:>5.1f}天: {len(sub):>5}笔  {np.median(r):>+6.2f}%中位  {(r>0).mean()*100:>5.0f}%胜率  {r.mean():>+6.2f}%均")
    
    all_signals.extend(cfg_rets)

# 保存
os.makedirs(os.path.dirname(OUT), exist_ok=True)
pd.DataFrame(all_signals).to_csv(OUT, index=False)
print(f"\n{'='*60}")
print(f"全部完成！结果: {OUT}")
print(f"总耗时: {time.time()-t0:.0f}s")
print(f"总记录: {len(all_signals)} 条")
