#!/usr/bin/env python3
"""S007: 两周10%短线策略 — Studio版"""
import sqlite3, pandas as pd, numpy as np, os, time

t0 = time.time()
DB = os.path.expanduser('~/stock-data/stock_all.db')
OUT = os.path.expanduser('~/stock-tools/strategy-lab/sessions/2026-07-15-S007-2Week/')

print(f"S007 run at {time.strftime('%H:%M:%S')}")
os.makedirs(OUT, exist_ok=True)

# 股票池
conn = sqlite3.connect(DB); cur = conn.cursor()
cur.execute("SELECT DISTINCT ts_code FROM stock_list WHERE name NOT LIKE '%ST%' AND name NOT LIKE '%*ST%' AND name NOT LIKE '%退%'")
clean = set(r[0] for r in cur.fetchall())
cur.execute("""SELECT ts_code FROM daily WHERE trade_date>='20240101' AND trade_date<'20260101'
    GROUP BY ts_code ORDER BY AVG(CAST(vol AS REAL)*CAST(close AS REAL)) DESC LIMIT 1000""")
pool = [r[0] for r in cur.fetchall() if r[0] in clean]
cs = ','.join(f"'{c}'" for c in pool)

# 加载日线 + 沪深300
daily_all = pd.read_sql(f"""SELECT ts_code, trade_date,
    CAST(open AS REAL) as o, CAST(close AS REAL) as c,
    CAST(high AS REAL) as h, CAST(low AS REAL) as l, CAST(vol AS REAL) as v
    FROM daily WHERE ts_code IN ({cs}) AND trade_date>='20210101'
    ORDER BY ts_code, trade_date""", conn)
daily_all['trade_date'] = pd.to_datetime(daily_all['trade_date'], format='%Y%m%d')
conn.close()

import akshare as ak
idx = ak.stock_zh_index_daily(symbol='sh000300')
idx['date'] = pd.to_datetime(idx['date'])
idx['ma200'] = idx['close'].rolling(200).mean()
idx['is_bull'] = idx['close'] > idx['ma200']
bull_set = set(idx[idx['is_bull']]['date'])

close_pv = daily_all.pivot(index='trade_date', columns='ts_code', values='c').sort_index()
print(f"池: {len(pool)}只, 数据: {len(daily_all)}行")

# ============================================================
# 策略方案
# ============================================================
HOLD = 10  # 持有10个交易日（约2周）

def detect_signals(data, strategy_name):
    """检测信号，返回 (signal_dict, trades_list)"""
    signals = []
    trades_out = []
    
    for tc in pool:
        sd = data[data['ts_code']==tc].sort_values('trade_date').reset_index(drop=True)
        if len(sd) < 260: continue
        c = sd['c'].values; o = sd['o'].values; h = sd['h'].values
        l = sd['l'].values; v = sd['v'].values; dates = list(sd['trade_date'])
        
        # 预计算
        ret_1d = np.diff(c, prepend=c[0]) / np.maximum(c, 1e-10) * 100
        ret_5d = c / pd.Series(c).shift(5).values - 1
        ret_10d = c / pd.Series(c).shift(10).values - 1
        ret_20d = c / pd.Series(c).shift(20).values - 1
        
        gain = np.where(np.diff(c, prepend=c[0])>0, np.diff(c, prepend=c[0]), 0)
        loss = np.where(np.diff(c, prepend=c[0])<0, -np.diff(c, prepend=c[0]), 0)
        avg_g = pd.Series(gain).rolling(14).mean().values
        avg_l = pd.Series(loss).rolling(14).mean().values
        rsi = 100 - 100 / (1 + avg_g / np.maximum(avg_l, 1e-10))
        
        ma5 = pd.Series(c).rolling(5).mean().values
        ma10 = pd.Series(c).rolling(10).mean().values
        ma20 = pd.Series(c).rolling(20).mean().values
        ma60 = pd.Series(c).rolling(60).mean().values
        
        vma5 = pd.Series(v).rolling(5).mean().values
        vma20 = pd.Series(v).rolling(20).mean().values
        
        high_20 = pd.Series(h).rolling(20).max().values
        high_60 = pd.Series(h).rolling(60).max().values
        
        bb_mid = ma20
        bb_std = pd.Series(c).rolling(20).std().values
        bb_upper = bb_mid + 2*bb_std
        bb_width = 2*bb_std / bb_mid
        
        for i in range(259, len(c)):
            if i + HOLD >= len(c): continue
            
            # 牛市过滤
            is_bull = dates[i] in bull_set
            
            # ======== 策略1: 量价齐升突破 ========
            s1_vol = v[i] > vma20[i] * 2.0  # 量2倍
            s1_break = c[i] >= high_20[i-1] * 0.995  # 接近20日新高
            s1_mom = ret_5d[i] > 0.03 if not np.isnan(ret_5d[i]) else False  # 5天涨>3%
            s1_rsi = 40 < rsi[i] < 85 if not np.isnan(rsi[i]) else False  # RSI合理
            s1_above_ma = c[i] > ma20[i] and c[i] > ma60[i] if not np.isnan(ma20[i]) and not np.isnan(ma60[i]) else False
            
            cond_s1 = all([s1_vol, s1_break, s1_mom, s1_rsi, s1_above_ma, is_bull])
            
            # ======== 策略2: 连阳加速 ========
            s2_consec = all(c[i-j] > o[i-j] for j in range(3))  # 3连阳
            s2_vol_up = v[i] > v[i-1] and v[i-1] > v[i-2]  # 量递增
            s2_close_high = all(c[i-j] > (c[i-j]+l[i-j])/2 * 1.01 for j in range(3))  # 收在高位
            s2_rsi = 50 < rsi[i] < 80 if not np.isnan(rsi[i]) else False
            s2_trend = c[i] > ma20[i] and ma20[i] > ma60[i] if not np.isnan(ma20[i]) and not np.isnan(ma60[i]) else False
            
            cond_s2 = all([s2_consec, s2_vol_up, s2_close_high, s2_rsi, s2_trend, is_bull])
            
            # ======== 策略3: 布林带突破 ========
            s3_bb = c[i] > bb_upper[i] * 1.005 if not np.isnan(bb_upper[i]) else False
            s3_vol = v[i] > vma5[i] * 1.5
            s3_narrow = bb_width[i-10] < bb_width[i] and bb_width[i-10] < 0.15 if i>=10 and not np.isnan(bb_width[i]) else True
            s3_rsi = rsi[i] > 55 if not np.isnan(rsi[i]) else True
            pos_chg = ret_1d[i] > 2 if i>0 else True  # 今日涨>2%
            
            cond_s3 = all([s3_bb, s3_vol, s3_narrow, s3_rsi, pos_chg, is_bull])
            
            # ======== 策略4: 强势回调买入 ========
            s4_pullback = ret_10d[i] > -0.08 and ret_10d[i] < -0.03 if not np.isnan(ret_10d[i]) else False
            s4_uptrend = ret_20d[i] > 0.10 if not np.isnan(ret_20d[i]) else False  # 20天涨>10%（强势股）
            s4_reversal = c[i] > o[i] and c[i] > c[i-1]  # 今日收阳反弹
            s4_vol_low = v[i] < vma5[i]  # 缩量回调
            
            cond_s4 = all([s4_pullback, s4_uptrend, s4_reversal, s4_vol_low, is_bull])
            
            # 检查哪个策略触发
            for cond, sname in [(cond_s1, '量价突破'), (cond_s2, '连阳加速'), 
                                (cond_s3, '布林突破'), (cond_s4, '强势回调')]:
                if not cond: continue
                
                entry = c[i]
                # 计算10天收益路径
                prices = [float(c[i+j]) for j in range(HOLD+1)]
                
                # 基础收益（持有到期）
                base_ret = (prices[-1] / entry - 1) * 100
                
                # 止盈+10% 止损-7%
                tp_hit = False; sl_hit = False; tp_ret = base_ret; exit_day = HOLD
                for j in range(1, len(prices)):
                    r = (prices[j] / entry - 1) * 100
                    if r >= 10:
                        tp_ret = 10; exit_day = j; tp_hit = True; break
                    if r <= -7:
                        tp_ret = -7; exit_day = j; sl_hit = True; break
                
                signals.append({
                    'strategy': sname, 'ts_code': tc, 'date': str(dates[i]),
                    'return': base_ret, 'tp_return': tp_ret,
                    'tp_hit': tp_hit, 'sl_hit': sl_hit, 'exit_day': exit_day,
                    'rsi': rsi[i] if not np.isnan(rsi[i]) else 50,
                    'vol_ratio': v[i]/vma20[i] if vma20[i]>0 else 1,
                })
                break  # 一个信号只记一次（优先级s1>s2>s3>s4）
    
    return signals

signals = detect_signals(daily_all, 'all')

# 去重（同股票20天内只取一次）
signals.sort(key=lambda x: (x['ts_code'], x['date']))
deduped = []; last = {}
for s in signals:
    ts = s['ts_code']; d = pd.Timestamp(s['date'])
    if ts in last and (d - last[ts]).days < 15:
        continue
    deduped.append(s); last[ts] = d

df_sig = pd.DataFrame(deduped)
print(f"\n信号: {len(signals)} → 去重后: {len(deduped)}")

# 按策略汇总
for sname in ['量价突破', '连阳加速', '布林突破', '强势回调']:
    sub = df_sig[df_sig['strategy']==sname]
    if len(sub) < 3: continue
    r = sub['return']; tp = sub['tp_return']
    print(f'\n=== {sname}: {len(sub)}笔 ===')
    print(f'  持有到期: 胜率{(r>0).mean()*100:.0f}%  中位{np.median(r):+.2f}%  均{r.mean():+.2f}%')
    print(f'  止盈+10%/止损-7%: 胜率{(tp>0).mean()*100:.0f}%  中位{np.median(tp):+.2f}%  均{tp.mean():+.2f}%')
    print(f'  赚>10%: {(r>10).mean()*100:.0f}%  亏>7%: {(r<-7).mean()*100:.0f}%')
    print(f'  触发止盈: {sub["tp_hit"].sum()}笔({sub["tp_hit"].mean()*100:.0f}%)  触发止损: {sub["sl_hit"].sum()}笔({sub["sl_hit"].mean()*100:.0f}%)')

# 总体
r_all = df_sig['return']; tp_all = df_sig['tp_return']
print(f'\n{"="*60}')
print(f'全部: {len(df_sig)}笔')
print(f'  持有到期: 胜率{(r_all>0).mean()*100:.0f}%  中位{np.median(r_all):+.2f}%  均{r_all.mean():+.2f}%')
print(f'  止盈+10%/止损-7%: 胜率{(tp_all>0).mean()*100:.0f}%  中位{np.median(tp_all):+.2f}%  均{tp_all.mean():+.2f}%')
print(f'  触及止盈: {df_sig["tp_hit"].sum()}笔({df_sig["tp_hit"].mean()*100:.0f}%)  触及止损: {df_sig["sl_hit"].sum()}笔({df_sig["sl_hit"].mean()*100:.0f}%)')

# 按年
df_sig['year'] = pd.to_datetime(df_sig['date']).dt.year
for y in [2022,2023,2024,2025]:
    sub = df_sig[df_sig['year']==y]
    if len(sub) < 3: continue
    print(f'  {y}: {len(sub)}笔  中位{np.median(sub["tp_return"]):+.2f}%  胜率{(sub["tp_return"]>0).mean()*100:.0f}%')

df_sig.to_csv(f'{OUT}signals.csv', index=False)
print(f'\n已完成, {time.time()-t0:.0f}s')
PYEOF