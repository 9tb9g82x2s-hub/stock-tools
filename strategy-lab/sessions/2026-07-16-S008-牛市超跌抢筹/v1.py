#!/usr/bin/env python3
"""S008 v1: 牛市超跌抢筹股 — 均值回归策略
核心：牛市环境 + 个股超跌 + 资金抢筹确认（放量阳线）→ 10天持有"""
import sqlite3, pandas as pd, numpy as np, os, time, random

t0 = time.time()
DB = os.path.expanduser('~/stock-data/stock_all.db')
OUT = os.path.expanduser('~/stock-tools/strategy-lab/sessions/2026-07-16-S008-牛市超跌抢筹/')
os.makedirs(OUT, exist_ok=True)

random.seed(42); np.random.seed(42)
print(f"S008 v1 牛市超跌抢筹 — {time.strftime('%H:%M:%S')}")

# ============================================================
# 1. 股票池：成交额Top 800，排除ST
# ============================================================
conn = sqlite3.connect(DB); cur = conn.cursor()
cur.execute("SELECT DISTINCT ts_code FROM stock_list WHERE name NOT LIKE '%ST%' AND name NOT LIKE '%*ST%' AND name NOT LIKE '%退%'")
clean = set(r[0] for r in cur.fetchall())
cur.execute("""SELECT ts_code FROM daily WHERE trade_date>='20240101' AND trade_date<'20260101'
    GROUP BY ts_code ORDER BY AVG(CAST(vol AS REAL)*CAST(close AS REAL)) DESC LIMIT 800""")
pool = [r[0] for r in cur.fetchall() if r[0] in clean]
cs = ','.join(f"'{c}'" for c in pool)
print(f"股票池: {len(pool)}只")

daily_all = pd.read_sql(f"""SELECT ts_code, trade_date,
    CAST(open AS REAL) as o, CAST(close AS REAL) as c,
    CAST(high AS REAL) as h, CAST(low AS REAL) as l, CAST(vol AS REAL) as v
    FROM daily WHERE ts_code IN ({cs}) AND trade_date>='20210101'
    ORDER BY ts_code, trade_date""", conn)
daily_all['trade_date'] = pd.to_datetime(daily_all['trade_date'], format='%Y%m%d')
conn.close()

# ============================================================
# 2. 牛市判断：沪深300 > MA200
# ============================================================
import akshare as ak
idx = ak.stock_zh_index_daily(symbol='sh000300')
idx['date'] = pd.to_datetime(idx['date'])
idx['ma200'] = idx['close'].rolling(200).mean()
idx['is_bull'] = idx['close'] > idx['ma200']
bull_set = set(idx[idx['is_bull']]['date'])
print(f"牛市占比: {len(bull_set)}/{len(idx)} = {len(bull_set)/len(idx)*100:.0f}%")

# ============================================================
# 3. 策略参数
# ============================================================
HOLD = 10          # 持有10个交易日
DROP_10D = -0.12   # 10日跌幅 > 12%
DROP_20D = -0.15   # 20日跌幅 > 15%
RSI_MAX = 35       # RSI < 35（超卖）
VOL_MIN = 1.5      # 量 > 20日均量*1.5

print(f"参数: 10日跌>{abs(DROP_10D)*100:.0f}% 20日跌>{abs(DROP_20D)*100:.0f}% "
      f"RSI<{RSI_MAX} 量>{VOL_MIN}x 持有{HOLD}天 无止盈止损")

# ============================================================
# 4. 扫描信号
# ============================================================
signals = []

for ti, tc in enumerate(pool):
    sd = daily_all[daily_all['ts_code']==tc].sort_values('trade_date').reset_index(drop=True)
    if len(sd) < 260: continue
    c = sd['c'].values; o = sd['o'].values; h = sd['h'].values
    l = sd['l'].values; v = sd['v'].values; dates = list(sd['trade_date'])
    
    ret_10d = c / pd.Series(c).shift(10).values - 1
    ret_20d = c / pd.Series(c).shift(20).values - 1
    ret_1d = np.diff(c, prepend=c[0]) / np.maximum(np.abs(c), 1e-10) * 100
    
    gain = np.where(np.diff(c, prepend=c[0])>0, np.diff(c, prepend=c[0]), 0)
    loss = np.where(np.diff(c, prepend=c[0])<0, -np.diff(c, prepend=c[0]), 0)
    avg_g = pd.Series(gain).rolling(14).mean().values
    avg_l = pd.Series(loss).rolling(14).mean().values
    rsi = 100 - 100 / (1 + avg_g / np.maximum(avg_l, 1e-10))
    
    vma20 = pd.Series(v).rolling(20).mean().values
    ma60 = pd.Series(c).rolling(60).mean().values
    
    for i in range(259, len(c)):
        if i + HOLD >= len(c): continue
        if dates[i] not in bull_set: continue
        
        # --- 超跌条件 ---
        if np.isnan(ret_10d[i]) or np.isnan(ret_20d[i]): continue
        if ret_10d[i] > DROP_10D: continue
        if ret_20d[i] > DROP_20D: continue
        
        # --- 排除崩盘股（近3天有跌停） ---
        crash = any(ret_1d[max(0,i-3):i+1] < -9.5)
        if crash: continue
        
        # --- 抢筹确认 ---
        if c[i] <= o[i]: continue  # 必须收阳
        
        day_range = h[i] - l[i]
        close_pos = (c[i] - l[i]) / day_range if day_range > 0 else 0.5
        if close_pos < 0.5: continue  # 收在日内上半区
        
        if np.isnan(vma20[i]) or vma20[i] <= 0: continue
        vol_ratio = v[i] / vma20[i]
        if vol_ratio < VOL_MIN: continue  # 放量不足
        
        if np.isnan(rsi[i]) or rsi[i] > RSI_MAX: continue  # 未超卖
        
        # --- 计算收益 ---
        entry = c[i]
        prices = [float(c[i+j]) for j in range(HOLD+1)]
        base_ret = (prices[-1] / entry - 1) * 100
        
        signals.append({
            'ts_code': tc, 'date': str(dates[i]),
            'return': base_ret,
            'rsi': float(rsi[i]), 'vol_ratio': float(vol_ratio),
            'drop_10d': float(ret_10d[i]*100), 'drop_20d': float(ret_20d[i]*100),
        })
    
    if (ti+1) % 100 == 0:
        print(f"  进度: {ti+1}/{len(pool)} | 信号: {len(signals)}")

# ============================================================
# 5. 去重（同股票15天内只取第一次）
# ============================================================
signals.sort(key=lambda x: (x['ts_code'], x['date']))
deduped = []; last = {}
for s in signals:
    ts = s['ts_code']; d = pd.Timestamp(s['date'])
    if ts in last and (d - last[ts]).days < 15:
        continue
    deduped.append(s); last[ts] = d

df_sig = pd.DataFrame(deduped)
print(f"\n信号: {len(signals)} → 去重后: {len(deduped)}")

if len(df_sig) == 0:
    print("⚠️ 无信号！需放宽条件。")
    exit()

# ============================================================
# 6. 结果分析
# ============================================================
br = df_sig['return']
n = len(df_sig)

print(f"\n{'='*60}")
print(f'【S008 v1 — 全部 {n} 笔信号（持有{HOLD}天，无止盈止损）】')
print(f'  持有到期: 胜率{(br>0).mean()*100:.0f}%  中位{np.median(br):+.2f}%  均{br.mean():+.2f}%')
print(f'  赚>5%: {(br>5).mean()*100:.0f}%  赚>10%: {(br>10).mean()*100:.0f}%  赚>15%: {(br>15).mean()*100:.0f}%')
print(f'  亏>5%: {(br<-5).mean()*100:.0f}%  亏>10%: {(br<-10).mean()*100:.0f}%')
print(f'  最大单笔: {br.max():+.1f}%  最小单笔: {br.min():+.1f}%')

# 按年
df_sig['year'] = pd.to_datetime(df_sig['date']).dt.year
print(f"\n--- 按年 ---")
for y in sorted(df_sig['year'].unique()):
    sub = df_sig[df_sig['year']==y]
    if len(sub) < 2: continue
    yr = sub['return']
    print(f"  {y}: {len(sub)}笔  中位{np.median(yr):+.2f}%  胜率{(yr>0).mean()*100:.0f}%  "
          f"均{yr.mean():+.2f}%  最大{yr.max():+.1f}%  最小{yr.min():+.1f}%")

# 最近信号
print(f"\n--- 最近信号 (2025+) ---")
recent = df_sig[df_sig['year'] >= 2025].head(20)
for _, row in recent.iterrows():
    flag = '⬆' if row['return'] > 0 else '⬇'
    print(f"  {row['date'][:10]} {row['ts_code']} | "
          f"跌{abs(row['drop_10d']):.0f}%/{abs(row['drop_20d']):.0f}% | "
          f"RSI{row['rsi']:.0f} 量{row['vol_ratio']:.1f}x | "
          f"收益{row['return']:+.1f}% {flag}")

# ============================================================
# 7. 随机基准对比
# ============================================================
print(f"\n--- 随机基准 ---")
rand_returns = []
for _ in range(1000):
    tc = random.choice(pool)
    sd = daily_all[daily_all['ts_code']==tc]
    if len(sd) < 260 + HOLD: continue
    ri = random.randint(259, len(sd)-HOLD-1)
    entry = float(sd.iloc[ri]['c'])
    exit_p = float(sd.iloc[ri+HOLD]['c'])
    rand_returns.append((exit_p/entry - 1) * 100)

if rand_returns:
    rr = np.array(rand_returns)
    print(f"  随机(n={len(rr)}): 中位{np.median(rr):+.2f}%  胜率{(rr>0).mean()*100:.0f}%  均{rr.mean():+.2f}%")
    print(f"  策略超额(中位): {np.median(br)-np.median(rr):+.2f}%")

# 保存
df_sig.to_csv(f'{OUT}signals.csv', index=False)
print(f"\n✅ 已保存: {OUT}signals.csv")
print(f"耗时: {time.time()-t0:.0f}s")
