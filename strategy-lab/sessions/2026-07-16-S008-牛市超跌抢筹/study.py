#!/usr/bin/env python3
"""
S008 牛市超跌抢筹 — 周期特征研究
完整追踪：牛市创新高 → 下跌通道 → 止跌 → 反弹 → 后续涨幅
多周期：日线(量/价/OBV/RSI/MA) + 周线 + 月线
"""
import sqlite3, pandas as pd, numpy as np, os, time, warnings
warnings.filterwarnings('ignore')

t0 = time.time()
DB = os.path.expanduser('~/stock-data/stock_all.db')
OUT = os.path.expanduser('~/stock-tools/strategy-lab/sessions/2026-07-16-S008-牛市超跌抢筹/')
os.makedirs(OUT, exist_ok=True)
np.random.seed(42)

print(f"S008 周期特征研究 — {time.strftime('%H:%M:%S')}")
print("=" * 60)

# ============================================================
# 1. 数据加载
# ============================================================
conn = sqlite3.connect(DB); cur = conn.cursor()
cur.execute("SELECT DISTINCT ts_code FROM stock_list WHERE name NOT LIKE '%ST%' AND name NOT LIKE '%*ST%' AND name NOT LIKE '%退%'")
clean = set(r[0] for r in cur.fetchall())
cur.execute("""SELECT ts_code FROM daily WHERE trade_date>='20240101' AND trade_date<'20260101'
    GROUP BY ts_code ORDER BY AVG(CAST(vol AS REAL)*CAST(close AS REAL)) DESC LIMIT 600""")
pool = [r[0] for r in cur.fetchall() if r[0] in clean]
cs = ','.join(f"'{c}'" for c in pool)
print(f"股票池: {len(pool)}只")

daily = pd.read_sql(f"""SELECT ts_code, trade_date,
    CAST(open AS REAL) as o, CAST(close AS REAL) as c,
    CAST(high AS REAL) as h, CAST(low AS REAL) as l, CAST(vol AS REAL) as v
    FROM daily WHERE ts_code IN ({cs}) AND trade_date>='20180101'
    ORDER BY ts_code, trade_date""", conn)
daily['trade_date'] = pd.to_datetime(daily['trade_date'], format='%Y%m%d')
conn.close()
print(f"日线: {len(daily)}行, {daily['trade_date'].min().date()} ~ {daily['trade_date'].max().date()}")

# ============================================================
# 2. 牛市判断
# ============================================================
import akshare as ak
idx = ak.stock_zh_index_daily(symbol='sh000300')
idx['date'] = pd.to_datetime(idx['date'])
idx['ma200'] = idx['close'].rolling(200).mean()
idx['is_bull'] = idx['close'] > idx['ma200']
bull_set = set(idx[idx['is_bull']]['date'])
print(f"牛市天数: {len(bull_set)}/{len(idx)} ({len(bull_set)/len(idx)*100:.0f}%)")

# ============================================================
# 3. 识别周期：牛市创新高 → 超跌 → 止跌 → 反弹
# ============================================================

# 辅助：计算技术指标
def calc_indicators(c, o, h, l, v, dates):
    """返回指标DataFrame"""
    n = len(c)
    df = pd.DataFrame(index=range(n))
    
    # 原始量价
    df['c'] = c; df['o'] = o; df['h'] = h; df['l'] = l; df['v'] = v
    
    # 日涨跌幅
    df['ret_1d'] = np.diff(c, prepend=c[0]) / np.maximum(np.abs(c), 1e-10) * 100
    
    # 滚动收益率
    for w in [5, 10, 20, 60, 120, 250]:
        df[f'ret_{w}d'] = c / pd.Series(c).shift(w).values - 1
    
    # 均线
    for w in [5, 10, 20, 60, 120, 250]:
        df[f'ma{w}'] = pd.Series(c).rolling(w).mean().values
    
    # 距离均线
    for w in [20, 60, 120, 250]:
        df[f'dist_ma{w}'] = (c - df[f'ma{w}']) / df[f'ma{w}'] * 100
    
    # RSI(14)
    gain = np.where(np.diff(c, prepend=c[0]) > 0, np.diff(c, prepend=c[0]), 0)
    loss = np.where(np.diff(c, prepend=c[0]) < 0, -np.diff(c, prepend=c[0]), 0)
    avg_g = pd.Series(gain).rolling(14).mean().values
    avg_l = pd.Series(loss).rolling(14).mean().values
    df['rsi'] = 100 - 100 / (1 + avg_g / np.maximum(avg_l, 1e-10))
    
    # 量
    df['vma5'] = pd.Series(v).rolling(5).mean().values
    df['vma20'] = pd.Series(v).rolling(20).mean().values
    df['vol_ratio'] = v / df['vma20']
    
    # OBV
    obv = np.zeros(n)
    for i in range(1, n):
        if c[i] > c[i-1]:
            obv[i] = obv[i-1] + v[i]
        elif c[i] < c[i-1]:
            obv[i] = obv[i-1] - v[i]
        else:
            obv[i] = obv[i-1]
    df['obv'] = obv
    df['obv_ma20'] = pd.Series(obv).rolling(20).mean().values
    df['obv_trend'] = (obv - pd.Series(obv).rolling(20).mean()) / pd.Series(obv).rolling(20).mean() * 100
    
    # 布林带
    bb_mid = pd.Series(c).rolling(20).mean().values
    bb_std = pd.Series(c).rolling(20).std().values
    df['bb_width'] = 2 * bb_std / bb_mid * 100
    
    # 波动率
    df['volatility'] = pd.Series(df['ret_1d']).rolling(20).std().values
    
    # K线特征
    df['body_pct'] = (c - o) / np.maximum(o, 1e-10) * 100  # 实体比例
    df['upper_shadow'] = (h - np.maximum(c, o)) / np.maximum(h - l, 1e-10) * 100  # 上影线
    df['lower_shadow'] = (np.minimum(c, o) - l) / np.maximum(h - l, 1e-10) * 100  # 下影线
    
    # 阶段性高低点（120日窗口）
    df['high_120'] = pd.Series(h).rolling(120).max().values
    df['low_120'] = pd.Series(l).rolling(120).min().values
    df['high_250'] = pd.Series(h).rolling(250).max().values
    df['from_high'] = (c - df['high_120']) / df['high_120'] * 100
    
    df['date'] = dates
    return df

# ============================================================
# 4. 遍历每只股票，找完整周期
# ============================================================

MIN_DRAWDOWN = 0.25   # 超跌阈值：跌超25%
MIN_REBOUND = 0.10    # 反弹确认：涨超10%
MIN_CYCLE_DAYS = 20   # 至少持续20个交易日（避免噪音）
MAX_CYCLE_DAYS = 500  # 不超过500天

all_cycles = []

for ti, tc in enumerate(pool):
    sd = daily[daily['ts_code'] == tc].sort_values('trade_date').reset_index(drop=True)
    if len(sd) < 500: continue
    
    c = sd['c'].values; o = sd['o'].values; h = sd['h'].values
    l = sd['l'].values; v = sd['v'].values; dates = list(sd['trade_date'])
    df = calc_indicators(c, o, h, l, v, dates)
    
    # 找牛市中的局部高点（创新高 + 在MA200上方 + 在牛市中）
    # 局部高点：price > 前后各30天的最高价（近似阶段新高）
    n = len(c)
    peak_candidates = []
    for i in range(120, n-40):
        if dates[i] not in bull_set: continue
        if np.isnan(df['ma250'].iloc[i]) or c[i] < df['ma250'].iloc[i]: continue
        
        # 前后30天的局部高点
        lookback = min(60, i)
        lookforward = min(60, n-i-1)
        if c[i] < np.max(h[i-lookback:i]): continue  # 不是近期新高
        if lookforward > 0 and c[i] < np.max(h[i+1:i+1+lookforward]): continue  # 之后有更高
        
        peak_candidates.append(i)
    
    # 从每个高点开始，追踪下跌→止跌→反弹周期
    used_ranges = []  # 避免重叠周期
    
    for peak_idx in peak_candidates:
        # 检查是否与已有周期重叠
        if any(peak_idx >= r[0] and peak_idx <= r[1] for r in used_ranges):
            continue
        
        peak_price = c[peak_idx]
        peak_date = dates[peak_idx]
        
        # 找超跌点（跌幅>25%）
        trough_candidate = None
        for j in range(peak_idx + MIN_CYCLE_DAYS, min(n, peak_idx + MAX_CYCLE_DAYS)):
            dd = c[j] / peak_price - 1
            if dd <= -MIN_DRAWDOWN:
                trough_candidate = j
                break
        
        if trough_candidate is None: continue
        
        # 找止跌点（超跌后的最低点）
        search_end = min(n, trough_candidate + 120)
        trough_idx = trough_candidate + np.argmin(c[trough_candidate:search_end])
        trough_price = c[trough_idx]
        trough_date = dates[trough_idx]
        
        # 阶段A: 下跌通道 (peak → trough)
        peak_to_trough = trough_idx - peak_idx
        max_dd = trough_price / peak_price - 1
        
        # 找反弹点（从止跌点涨超10%）
        rebound_idx = None
        for j in range(trough_idx + MIN_CYCLE_DAYS, min(n, trough_idx + MAX_CYCLE_DAYS)):
            rb = c[j] / trough_price - 1
            if rb >= MIN_REBOUND:
                rebound_idx = j
                break
        
        if rebound_idx is None: continue
        
        rebound_price = c[rebound_idx]
        rebound_date = dates[rebound_idx]
        
        # 如果有反弹，标记这个周期已使用
        used_ranges.append((peak_idx, rebound_idx))
        
        # ============================================================
        # 提取各阶段特征
        # ============================================================
        
        # --- A. 下跌通道特征 (peak → trough) ---
        phase_a = df.iloc[peak_idx:trough_idx+1]
        
        # 下跌速度
        daily_drop_rate = (trough_price / peak_price - 1) / peak_to_trough * 100  # % per day
        
        # 下跌中的量特征
        fall_vol_mean = phase_a['v'].mean()
        fall_vol_trend = np.polyfit(range(len(phase_a)), phase_a['v'].values, 1)[0]  # 量趋势斜率
        
        # 前期上涨量（peak之前60天）
        pre_up = df.iloc[max(0, peak_idx-60):peak_idx]
        pre_up_vol = pre_up['v'].mean() if len(pre_up) > 0 else fall_vol_mean
        
        # OBV在下跌中变化
        if len(phase_a) >= 2:
            obv_change = phase_a['obv'].iloc[-1] / max(phase_a['obv'].iloc[0], 1) - 1
            obv_slope = np.polyfit(range(len(phase_a)), phase_a['obv'].values, 1)[0]
            obv_slope_norm = obv_slope / max(abs(phase_a['obv'].mean()), 1) * 100
        else:
            obv_change = 0; obv_slope_norm = 0
        
        # 加速下跌检测：后1/3跌幅 vs 前1/3
        n3 = len(phase_a) // 3
        if n3 > 0:
            first_third_drop = (phase_a['c'].iloc[n3] / phase_a['c'].iloc[0] - 1) if n3 > 0 else 0
            last_third_drop = (phase_a['c'].iloc[-1] / phase_a['c'].iloc[-n3] - 1) if n3 > 0 else 0
            accel = (last_third_drop - first_third_drop)  # 负值=加速下跌
        else:
            accel = 0
        
        # 波动率变化
        vol_before = df['volatility'].iloc[max(0, peak_idx-20):peak_idx].mean() if peak_idx >= 20 else 0
        vol_during = phase_a['volatility'].mean()
        
        # --- B. 止跌区域特征 (trough附近 ±5天) ---
        b_start = max(0, trough_idx - 5)
        b_end = min(n-1, trough_idx + 5)
        phase_b = df.iloc[b_start:b_end+1]
        
        # 止跌日当天特征
        trough_vol_ratio = df['vol_ratio'].iloc[trough_idx] if not np.isnan(df['vol_ratio'].iloc[trough_idx]) else 1
        trough_rsi = df['rsi'].iloc[trough_idx] if not np.isnan(df['rsi'].iloc[trough_idx]) else 50
        trough_body = df['body_pct'].iloc[trough_idx]
        trough_lower_shadow = df['lower_shadow'].iloc[trough_idx]  # 下影线比例
        
        # 是否锤子线（下影线>60%, 实体<2%）
        is_hammer = trough_lower_shadow > 60 and abs(trough_body) < 2
        
        # OBV底背离：价格新低但OBV不新低
        # 找止跌点前60天内的前一个低点
        obv_pre = df['obv'].iloc[max(0, trough_idx-60):trough_idx]
        if len(obv_pre) >= 10:
            pre_low_obv = obv_pre.min()
            obv_divergence = obv_pre.iloc[-1] > pre_low_obv * 1.02  # OBV在抬升
        else:
            obv_divergence = False
        
        # 缩量程度
        vol_shrink = df['vol_ratio'].iloc[trough_idx-5:trough_idx+1].mean() if trough_idx >= 5 else 1
        
        # 距离各均线
        dist_ma20 = df['dist_ma20'].iloc[trough_idx] if not np.isnan(df['dist_ma20'].iloc[trough_idx]) else 0
        dist_ma60 = df['dist_ma60'].iloc[trough_idx] if not np.isnan(df['dist_ma60'].iloc[trough_idx]) else 0
        dist_ma120 = df['dist_ma120'].iloc[trough_idx] if not np.isnan(df['dist_ma120'].iloc[trough_idx]) else 0
        dist_ma250 = df['dist_ma250'].iloc[trough_idx] if not np.isnan(df['dist_ma250'].iloc[trough_idx]) else 0
        
        # 布林带宽度
        bb_w = df['bb_width'].iloc[trough_idx] if not np.isnan(df['bb_width'].iloc[trough_idx]) else 0
        
        # K线收敛（止跌区振幅 vs 前20天）
        b_range = (phase_b['h'].max() - phase_b['l'].min()) / phase_b['c'].mean() * 100
        pre_range = df.iloc[max(0, trough_idx-20):max(0, trough_idx-10)]
        pre_range_val = (pre_range['h'].max() - pre_range['l'].min()) / pre_range['c'].mean() * 100 if len(pre_range) > 0 else b_range
        
        # --- C. 反弹阶段特征 (trough → rebound) ---
        phase_c = df.iloc[trough_idx:rebound_idx+1]
        rebound_days = rebound_idx - trough_idx
        rebound_return = c[rebound_idx] / trough_price - 1
        
        # 反弹速度
        daily_reb_rate = rebound_return / rebound_days * 100
        
        # 反弹量
        reb_vol_mean = phase_c['v'].mean()
        reb_vol_ratio = reb_vol_mean / fall_vol_mean if fall_vol_mean > 0 else 1  # vs 下跌期量
        
        # 首次放量日（反弹中vol > 20日均量*1.5的第一天）
        first_surge_day = None
        for j in range(trough_idx, rebound_idx+1):
            if not np.isnan(df['vol_ratio'].iloc[j]) and df['vol_ratio'].iloc[j] > 1.5:
                first_surge_day = j - trough_idx
                break
        
        # 反弹中突破MA20/MA60的日期
        break_ma20_day = None; break_ma60_day = None
        for j in range(trough_idx, rebound_idx+1):
            if break_ma20_day is None and not np.isnan(df['ma20'].iloc[j]) and c[j] > df['ma20'].iloc[j]:
                break_ma20_day = j - trough_idx
            if break_ma60_day is None and not np.isnan(df['ma60'].iloc[j]) and c[j] > df['ma60'].iloc[j]:
                break_ma60_day = j - trough_idx
        
        # OBV回升
        if len(phase_c) >= 2:
            obv_reb_change = phase_c['obv'].iloc[-1] / max(phase_c['obv'].iloc[0], 1) - 1
        else:
            obv_reb_change = 0
        
        # 反弹过程中的回踩（跌超3%的次数）
        pullback_count = 0
        for j in range(trough_idx + 3, rebound_idx):
            if df['ret_5d'].iloc[j] < -0.03 if not np.isnan(df['ret_5d'].iloc[j]) else False:
                pullback_count += 1
        
        # --- D. 后续涨幅 ---
        # 止跌后20/40/60/120天的收益
        fut_returns = {}
        for horizon in [20, 40, 60, 120, 250]:
            fut_idx = min(trough_idx + horizon, n-1)
            fut_returns[f'fwd_{horizon}d'] = c[fut_idx] / trough_price - 1
        
        # 是否回到前高
        back_to_peak = False
        back_days = None
        for j in range(trough_idx, min(n, trough_idx + 500)):
            if c[j] >= peak_price * 0.95:
                back_to_peak = True
                back_days = j - trough_idx
                break
        
        # 最大反弹幅度
        search_to = min(n, trough_idx + 250)
        max_reb = np.max(c[trough_idx:search_to]) / trough_price - 1
        
        # ============================================================
        # 记录周期
        # ============================================================
        all_cycles.append({
            'ts_code': tc, 'peak_date': str(peak_date.date()),
            'trough_date': str(trough_date.date()), 'rebound_date': str(rebound_date.date()),
            # 基本参数
            'peak_price': float(peak_price), 'trough_price': float(trough_price),
            'rebound_price': float(rebound_price),
            'max_drawdown': float(max_dd * 100),  # 最大回撤%
            'decline_days': peak_to_trough, 'rebound_days': rebound_days,
            # A: 下跌通道
            'daily_drop_rate': float(daily_drop_rate),  # %/day
            'fall_vol_mean': float(fall_vol_mean),
            'fall_vs_pre_vol': float(fall_vol_mean / pre_up_vol) if pre_up_vol > 0 else 1,
            'fall_vol_trend': float(fall_vol_trend),  # 量趋势(正=放量下跌)
            'obv_change': float(obv_change * 100),  # OBV变化%
            'obv_slope_norm': float(obv_slope_norm),  # OBV归一斜率
            'acceleration': float(accel * 100),  # 加速下跌(负=加速)
            'vol_change': float(vol_during - vol_before),  # 波动率变化
            # B: 止跌特征
            'trough_vol_ratio': float(trough_vol_ratio),
            'trough_rsi': float(trough_rsi),
            'trough_body_pct': float(trough_body),
            'is_hammer': is_hammer,
            'obv_divergence': obv_divergence,  # OBV底背离
            'vol_shrink_5d': float(vol_shrink),  # 5日缩量度
            'dist_ma20': float(dist_ma20), 'dist_ma60': float(dist_ma60),
            'dist_ma120': float(dist_ma120), 'dist_ma250': float(dist_ma250),
            'bb_width': float(bb_w),
            'range_shrink': float(b_range / pre_range_val) if pre_range_val > 0 else 1,
            # C: 反弹特征
            'daily_reb_rate': float(daily_reb_rate),
            'reb_vol_ratio': float(reb_vol_ratio),
            'first_surge_day': first_surge_day,
            'break_ma20_day': break_ma20_day, 'break_ma60_day': break_ma60_day,
            'obv_reb_change': float(obv_reb_change * 100),
            'pullback_count': pullback_count,
            # D: 后续涨幅
            'fwd_20d': float(fut_returns['fwd_20d'] * 100),
            'fwd_40d': float(fut_returns['fwd_40d'] * 100),
            'fwd_60d': float(fut_returns['fwd_60d'] * 100),
            'fwd_120d': float(fut_returns['fwd_120d'] * 100),
            'fwd_250d': float(fut_returns['fwd_250d'] * 100),
            'back_to_peak': back_to_peak,
            'back_days': back_days,
            'max_rebound': float(max_reb * 100),
        })
    
    if (ti+1) % 100 == 0:
        print(f"  进度: {ti+1}/{len(pool)} | 找到周期: {len(all_cycles)}")

# ============================================================
# 5. 汇总分析
# ============================================================
df_cycles = pd.DataFrame(all_cycles)
print(f"\n{'='*60}")
print(f"共找到 {len(df_cycles)} 个完整周期（牛市创新高→超跌25%+→止跌→反弹10%+）")
print(f"涉及 {df_cycles['ts_code'].nunique()} 只股票")

if len(df_cycles) == 0:
    print("⚠️ 未找到符合条件的周期！放宽条件重试。")
    exit()

# 基本统计
print(f"\n--- 基本分布 ---")
for col, label in [('max_drawdown', '最大回撤%'), ('decline_days', '下跌天数'),
                    ('rebound_days', '反弹天数'), ('trough_rsi', '止跌RSI')]:
    vals = df_cycles[col].dropna()
    print(f"  {label}: 中位{np.median(vals):.1f}  P25={np.percentile(vals,25):.1f}  "
          f"P75={np.percentile(vals,75):.1f}  min={vals.min():.1f}  max={vals.max():.1f}")

# A: 下跌通道特征
print(f"\n--- A. 下跌通道 ---")
for col, label in [
    ('daily_drop_rate', '日均跌幅%'), ('fall_vs_pre_vol', '下跌量/前期量'),
    ('fall_vol_trend', '量趋势(>0=放量跌)'), ('obv_change', 'OBV变化%'),
    ('acceleration', '加速下跌(负=加速)'), ('vol_change', '波动率变化'),
]:
    vals = df_cycles[col].dropna()
    print(f"  {label}: 中位{np.median(vals):.2f}  均{vals.mean():.2f}  >0占比{(vals>0).mean()*100:.0f}%")

# B: 止跌特征
print(f"\n--- B. 止跌特征 ---")
for col, label in [
    ('trough_vol_ratio', '止跌日量比'), ('vol_shrink_5d', '5日缩量度'),
    ('trough_body_pct', '止跌日实体%'), ('bb_width', '布林带宽%'),
    ('range_shrink', '振幅收敛比'), ('dist_ma20', '距MA20%'),
    ('dist_ma60', '距MA60%'), ('dist_ma120', '距MA120%'),
]:
    vals = df_cycles[col].dropna()
    print(f"  {label}: 中位{np.median(vals):.2f}  均{vals.mean():.2f}")
print(f"  锤子线占比: {df_cycles['is_hammer'].mean()*100:.0f}%")
print(f"  OBV底背离占比: {df_cycles['obv_divergence'].mean()*100:.0f}%")

# C: 反弹特征
print(f"\n--- C. 反弹特征 ---")
for col, label in [
    ('daily_reb_rate', '日均反弹%'), ('reb_vol_ratio', '反弹量/下跌量'),
    ('obv_reb_change', 'OBV回升%'), ('pullback_count', '回踩次数'),
]:
    vals = df_cycles[col].dropna()
    print(f"  {label}: 中位{np.median(vals):.2f}  均{vals.mean():.2f}")
print(f"  首次放量日: 中位{df_cycles['first_surge_day'].dropna().median():.0f}天")
print(f"  突破MA20日: 中位{df_cycles['break_ma20_day'].dropna().median():.0f}天  "
      f"(占比{df_cycles['break_ma20_day'].notna().mean()*100:.0f}%)")
print(f"  突破MA60日: 中位{df_cycles['break_ma60_day'].dropna().median():.0f}天  "
      f"(占比{df_cycles['break_ma60_day'].notna().mean()*100:.0f}%)")

# D: 后续涨幅
print(f"\n--- D. 后续涨幅 ---")
for col in ['fwd_20d', 'fwd_40d', 'fwd_60d', 'fwd_120d', 'fwd_250d', 'max_rebound']:
    vals = df_cycles[col].dropna()
    print(f"  {col}: 中位{np.median(vals):+.1f}%  P25={np.percentile(vals,25):+.1f}%  "
          f"P75={np.percentile(vals,75):+.1f}%  胜率{(vals>0).mean()*100:.0f}%")
print(f"  回到前高: {df_cycles['back_to_peak'].mean()*100:.0f}%  中位天数: {df_cycles['back_days'].dropna().median():.0f}天")

# ============================================================
# 6. 条件分组分析：什么止跌特征预示着好反弹？
# ============================================================
print(f"\n{'='*60}")
print(f"【关键：哪些止跌特征预示后续大涨？】")

df_cycles['is_good'] = df_cycles['fwd_60d'] > np.median(df_cycles['fwd_60d'].dropna())

for name, condition in [
    ('OBV底背离', df_cycles['obv_divergence']),
    ('锤子线', df_cycles['is_hammer']),
    ('RSI<30', df_cycles['trough_rsi'] < 30),
    ('量缩到0.5x', df_cycles['vol_shrink_5d'] < 0.5),
    ('距MA250>30%', df_cycles['dist_ma250'] < -30),
    ('加速下跌', df_cycles['acceleration'] < -1),  # 加速>1%/段
]:
    true_group = df_cycles[condition]
    if len(true_group) < 5: continue
    false_group = df_cycles[~condition]
    
    t_fwd60 = true_group['fwd_60d'].dropna().median()
    f_fwd60 = false_group['fwd_60d'].dropna().median()
    t_wr = (true_group['fwd_60d'] > 0).mean()
    f_wr = (false_group['fwd_60d'] > 0).mean()
    
    diff = t_fwd60 - f_fwd60
    marker = '🔴' if diff > 3 else '🟢' if diff > 0 else '⚪'
    print(f"  {marker} {name}(n={len(true_group)}): 60日后中位{t_fwd60:+.1f}% vs 否则{f_fwd60:+.1f}% "
          f"[差{diff:+.1f}%] 胜率{t_wr*100:.0f}% vs {f_wr*100:.0f}%")

# ============================================================
# 7. 保存
# ============================================================
df_cycles.to_csv(f'{OUT}cycles.csv', index=False)
print(f"\n✅ 已保存: {OUT}cycles.csv ({len(df_cycles)}条周期)")
print(f"耗时: {time.time()-t0:.0f}s")
