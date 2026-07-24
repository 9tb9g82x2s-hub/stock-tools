#!/usr/bin/env python3
"""
S003 — 牛市超跌掘金 回测
================================
大环境：上证距52周低点涨 > 15% → 牛市确认
选股池：Top 300 日均成交额，排除ST+亏损
信号条件（AND）：
  ① 趋势过滤：60日涨幅 > 0 AND close > MA120
  ② 超跌确认：20日高点回撤 > 25%
  ③ 止跌企稳：A(缩量:近5日均量/近20日<0.8) AND B(K线收敛:近20实体/前20实体<0.85)
分级：OBV背离 + 资金流趋势
持仓：40 / 60 / 80 天对比
"""
import sqlite3, pandas as pd, numpy as np, sys, os
from scipy import stats

DB = os.path.expanduser('~/stock-data/stock_all.db')

# ============================================================
# 1. 加载和预处理
# ============================================================
print("=" * 60)
print("S003 牛市超跌掘金 · 回测")
print("=" * 60)

conn = sqlite3.connect(DB)

# 加载A股日线
print("\n[1/5] 加载数据...")
df_d = pd.read_sql("""
    SELECT ts_code, trade_date,
           CAST(open AS REAL) o, CAST(high AS REAL) h,
           CAST(low AS REAL) l, CAST(close AS REAL) c,
           CAST(vol AS REAL) v, CAST(amount AS REAL) a
    FROM daily ORDER BY ts_code, trade_date
""", conn)

df_d['trade_date'] = pd.to_datetime(df_d['trade_date'], format='%Y%m%d')
print(f"  日线: {len(df_d)} 条, {df_d['ts_code'].nunique()} 只股票")

# 上证指数不在 daily 表中。用市场宽度判断牛熊：Top300中 > MA120 的占比 > 50% = 牛市
print(f"  牛熊判断: 用市场宽度(个股>MA120占比)")

# 加载黑名单
st_list = pd.read_sql("SELECT ts_code FROM blacklist_st", conn)['ts_code'].tolist()
loss_list = pd.read_sql("SELECT ts_code FROM blacklist_loss", conn)['ts_code'].tolist()
blacklist = set(st_list + loss_list)
print(f"  黑名单: ST {len(st_list)} + 亏损 {len(loss_list)} = {len(blacklist)} 只")

# 加载资金流
df_mf = pd.read_sql("""
    SELECT ts_code, trade_date, CAST(net_mf_amount AS REAL) net
    FROM moneyflow ORDER BY ts_code, trade_date
""", conn)
df_mf['trade_date'] = pd.to_datetime(df_mf['trade_date'], format='%Y%m%d')
print(f"  资金流: {len(df_mf)} 条")

conn.close()

# Pivot
close_pv = df_d.pivot(index='trade_date', columns='ts_code', values='c').sort_index()
vol_pv = df_d.pivot(index='trade_date', columns='ts_code', values='v').sort_index()
amount_pv = df_d.pivot(index='trade_date', columns='ts_code', values='a').sort_index()

# 所有日期
trade_dates = sorted(close_pv.index)

# ============================================================
# 2. 计算牛市择时（市场宽度法）
# ============================================================
print("\n[2/5] 计算市场牛熊(宽度法)...")

# 用所有非黑名单股票的收盘价计算MA120和宽度
all_stocks = [c for c in close_pv.columns if c not in blacklist]
print(f"  候选股票: {len(all_stocks)} 只")

# 预计算MA120
ma120_all = close_pv[all_stocks].rolling(120, min_periods=60).mean()

bull_dates = set()
for d in trade_dates:
    if d not in ma120_all.index:
        continue
    di = trade_dates.index(d)
    if di < 120:
        continue
    
    # 当日收盘 > MA120 的股票占比
    above = (close_pv.loc[d, all_stocks] > ma120_all.loc[d]).sum()
    total = close_pv.loc[d, all_stocks].notna().sum()
    if total < 100:
        continue
    breadth = above / total
    # 超过50%股票在MA120上方 = 牛市
    if breadth > 0.50:
        bull_dates.add(d)

print(f"  交易日: {len(trade_dates)} | 牛市天数: {len(bull_dates)} ({len(bull_dates)/len(trade_dates)*100:.0f}%)")

# ============================================================
# 3. 逐月扫描信号
# ============================================================
print("\n[3/5] 逐月扫描...")

# 生成每月扫描日（每月最后一个交易日）
monthly_dates = []
for y in range(2022, 2027):
    for m in range(1, 13):
        md = [d for d in trade_dates if d.year == y and d.month == m]
        if md:
            monthly_dates.append(md[-1])

print(f"  扫描月份: {len(monthly_dates)}")

# 预处理选股池：每期选 Top 300 日均成交额
def get_top300(trade_date, lookback=120):
    """选最近120个交易日内有数据的，按日均成交额排序 Top 300"""
    d_idx = trade_dates.index(trade_date)
    start_idx = max(0, d_idx - lookback)
    period = trade_dates[start_idx:d_idx + 1]
    
    amt = amount_pv.loc[period]
    # 只选在period内至少有30天数据的
    valid_cnt = amt.count()
    codes = valid_cnt[valid_cnt >= 30].index
    
    # 过滤黑名单
    codes = [c for c in codes if c not in blacklist]
    
    mean_amt = amt[codes].mean().sort_values(ascending=False)
    return mean_amt.head(300).index.tolist()


# 存储信号
signal_records = []  # 全部信号 + 收益
benchmark_records = []  # 随机基准

total_signals = 0
bull_months = 0

for si, sd in enumerate(monthly_dates):
    if si % 12 == 0:
        print(f"  进度: {sd.strftime('%Y-%m')}")
    
    # 牛市过滤
    if sd not in bull_dates:
        continue
    
    bull_months += 1
    
    # 选股池
    top_codes = get_top300(sd)
    if len(top_codes) < 50:
        continue
    
    # 准备工作
    d_idx = trade_dates.index(sd)
    if d_idx < 250:
        continue
    
    current_close = close_pv.loc[sd]
    
    # ----- 信号①: 趋势过滤 -----
    # 60日涨幅 > 0
    if d_idx >= 60:
        prev_60 = trade_dates[d_idx - 60]
        if prev_60 in close_pv.index:
            ret_60 = current_close / close_pv.loc[prev_60] - 1
        else:
            ret_60 = pd.Series(np.nan, index=top_codes)
    else:
        ret_60 = pd.Series(np.nan, index=top_codes)
    
    # MA120
    if d_idx >= 120:
        ma120_slice = close_pv.iloc[d_idx - 119:d_idx + 1]
        ma120 = ma120_slice.mean()
    else:
        ma120 = pd.Series(np.nan, index=top_codes)
    
    cond_trend = (ret_60 > 0) & (current_close > ma120)
    
    # ----- 信号②: 超跌确认 -----
    # 20日高点回撤 > 25%
    if d_idx >= 20:
        high20_slice = close_pv.iloc[d_idx - 19:d_idx + 1]
        high20 = high20_slice.max()
        drawdown = (current_close / high20 - 1) * 100
    else:
        drawdown = pd.Series(0, index=top_codes)
    
    cond_oversold = drawdown < -25
    
    # ----- 信号③: 止跌企稳 -----
    # A: 缩量 (近5日均量 / 近20日均量 < 0.8)
    if d_idx >= 20:
        vol5 = vol_pv.iloc[d_idx - 4:d_idx + 1].mean()
        vol20 = vol_pv.iloc[d_idx - 19:d_idx + 1].mean()
        vol_ratio = vol5 / (vol20 + 1e-10)
    else:
        vol_ratio = pd.Series(1.0, index=top_codes)
    
    cond_shrink = vol_ratio < 0.8
    
    # B: K线收敛 (近20日K线实体 / 前20日K线实体 < 0.85)
    body_ratios = {}
    for c in top_codes:
        cd = df_d[df_d['ts_code'] == c].sort_values('trade_date').set_index('trade_date')
        if len(cd) < 80 or sd not in cd.index:
            continue
        cd['body'] = abs(cd['c'] - cd['o']) / cd['c']
        cd['b20'] = cd['body'].rolling(20).mean()
        cd['bp20'] = cd['body'].shift(20).rolling(20).mean()
        cd['ratio'] = cd['b20'] / (cd['bp20'] + 1e-10)
        v = cd.loc[sd, 'ratio']
        body_ratios[c] = float(v) if not pd.isna(v) else np.nan
    
    body_ratio_s = pd.Series(body_ratios)
    cond_conv = body_ratio_s < 0.85
    
    # ----- OBV背离 -----
    obv_div = {}
    for c in top_codes:
        cls = close_pv[c].dropna()
        if len(cls) < 250:
            continue
        # 计算OBV
        obv_val = 0
        obv_vals = []
        pc = None
        for dd in cls.index:
            cl = cls.loc[dd]
            vo = vol_pv.loc[dd, c] if dd in vol_pv.index and c in vol_pv.columns else np.nan
            if pd.isna(cl) or pd.isna(vo):
                continue
            if pc is not None:
                if cl > pc:
                    obv_val += vo
                elif cl < pc:
                    obv_val -= vo
            pc = cl
            obv_vals.append(obv_val)
        if len(obv_vals) < 250:
            continue
        
        # 近250日价格斜率 和 OBV斜率
        y_obv = np.array(obv_vals[-250:])
        y_price = cls.values[-250:]
        x_arr = np.arange(250)
        
        valid = ~np.isnan(y_obv)
        if valid.sum() < 100:
            continue
        sl_obv, _, _, _, _ = stats.linregress(x_arr[valid], y_obv[valid])
        
        pn = y_price / y_price[0] if y_price[0] > 0 else y_price
        sl_price, _, _, _, _ = stats.linregress(x_arr, pn)
        
        # OBV背离 = 价格跌 + OBV涨/走平
        if sl_price < 0 and sl_obv >= 0:
            obv_div[c] = True
    
    # ----- 综合信号 -----
    cond_3 = cond_shrink & cond_conv  # A+B 都要
    signal = cond_trend & cond_oversold & cond_3
    
    # 只对 signal=True 的股票处理
    for c in top_codes:
        if c not in signal.index or signal[c] != True:
            continue
        if pd.isna(current_close[c]) or current_close[c] <= 0:
            continue
        
        # 分级
        has_obv = obv_div.get(c, False)
        level = '★★★' if has_obv else '★★☆'
        
        signal_records.append({
            'date': sd,
            'code': c,
            'level': level,
            'has_obv': has_obv,
            'entry': float(current_close[c])
        })
    
    total_signals += sum(1 for c in top_codes if c in signal.index and signal[c] == True)

print(f"\n  牛市月数: {bull_months}/{len(monthly_dates)}")
print(f"  总信号: {total_signals}")

# ============================================================
# 4. 计算收益（40/60/80天）
# ============================================================
print("\n[4/5] 计算持仓收益...")

results = {'40天': [], '60天': [], '80天': []}

for rec in signal_records:
    sd = rec['date']
    code = rec['code']
    entry = rec['entry']
    
    si = trade_dates.index(sd)
    
    for horizon_d, label in [(40, '40天'), (60, '60天'), (80, '80天')]:
        exit_idx = min(si + horizon_d, len(trade_dates) - 1)
        exit_date = trade_dates[exit_idx]
        
        if exit_date in close_pv.index and code in close_pv.columns:
            exit_price = close_pv.loc[exit_date, code]
            if pd.isna(exit_price) or exit_price <= 0:
                continue
            ret = (exit_price / entry - 1) * 100
            results[label].append({
                **rec,
                'horizon': horizon_d,
                'exit_date': exit_date,
                'return_pct': ret,
                'actual_days': exit_idx - si
            })

# 基准：随机选股
rng = np.random.default_rng(42)
for sd in monthly_dates:
    if sd not in bull_dates:
        continue
    top = get_top300(sd)
    if len(top) < 10:
        continue
    random_picks = rng.choice(top, min(5, len(top)), replace=False)
    si = trade_dates.index(sd)
    
    for code in random_picks:
        entry = close_pv.loc[sd, code]
        if pd.isna(entry) or entry <= 0:
            continue
        for horizon_d, label in [(40, '40天'), (60, '60天'), (80, '80天')]:
            exit_idx = min(si + horizon_d, len(trade_dates) - 1)
            exit_date = trade_dates[exit_idx]
            if exit_date in close_pv.index and code in close_pv.columns:
                exit_price = close_pv.loc[exit_date, code]
                if pd.isna(exit_price) or exit_price <= 0:
                    continue
                ret = (exit_price / entry - 1) * 100
                benchmark_records.append({
                    'date': sd, 'code': code, 'horizon': horizon_d,
                    'return_pct': ret, 'group': '随机基准'
                })

# ============================================================
# 5. 汇总报告
# ============================================================
print("\n[5/5] 汇总报告...\n")
print("=" * 70)

df_bench = pd.DataFrame(benchmark_records)

for label in ['40天', '60天', '80天']:
    recs = results[label]
    if not recs:
        print(f"\n  {label}: 无信号")
        continue
    
    r = np.array([x['return_pct'] for x in recs])
    N = len(r)
    wins = r[r > 0]
    losses = r[r <= 0]
    n_win = len(wins)
    n_loss = len(losses)
    avg_win = np.mean(wins) if n_win > 0 else 0
    avg_loss = np.mean(losses) if n_loss > 0 else 0
    wr = n_win / N if N > 0 else 0
    
    # 分级统计
    r_3 = np.array([x['return_pct'] for x in recs if x['level'] == '★★★'])
    r_2 = np.array([x['return_pct'] for x in recs if x['level'] == '★★☆'])
    
    # 分年统计
    years = sorted(set(x['date'].year for x in recs))
    
    print(f"\n{'─' * 70}")
    print(f"  持仓 {label}")
    print(f"{'─' * 70}")
    print(f"  信号数: {N}")
    print(f"  胜率: {wr*100:.1f}%  ({n_win}胜 / {n_loss}负)")
    print(f"  平均收益: {np.mean(r):+.2f}%")
    print(f"  中位数收益: {np.median(r):+.2f}%")
    print(f"  最大单笔: {np.max(r):+.2f}%")
    print(f"  最大亏损: {np.min(r):+.2f}%")
    print(f"  标准差: {np.std(r):.2f}%")
    if avg_loss != 0:
        print(f"  赔率: {abs(avg_win/avg_loss):.2f}:1")
    print(f"  均盈: {avg_win:+.2f}%  |  均亏: {avg_loss:+.2f}%")
    
    # 分级对比
    print(f"\n  分级对比:")
    if len(r_3) > 0:
        wr3 = (r_3 > 0).mean() * 100
        print(f"    ★★★ (OBV背离): {len(r_3)}次 | 胜率{wr3:.1f}% | 均值{np.mean(r_3):+.2f}%")
    if len(r_2) > 0:
        wr2 = (r_2 > 0).mean() * 100
        print(f"    ★★☆ (无背离):   {len(r_2)}次 | 胜率{wr2:.1f}% | 均值{np.mean(r_2):+.2f}%")
    
    # 分年
    print(f"\n  分年表现:")
    for y in years:
        yr = np.array([x['return_pct'] for x in recs if x['date'].year == y])
        if len(yr) > 0:
            w_yr = (yr > 0).mean() * 100
            print(f"    {y}: {len(yr)}次 | 胜率{w_yr:.0f}% | 均值{np.mean(yr):+.1f}%")
    
    # 基准对比
    ben_h = df_bench[df_bench['horizon'] == label]
    if len(ben_h) > 0:
        print(f"\n  随机基准对比:")
        print(f"    随机N={len(ben_h)} | 胜率{(ben_h['return_pct']>0).mean()*100:.0f}% | 均值{ben_h['return_pct'].mean():.2f}%")
        print(f"    策略超额: {np.mean(r) - ben_h['return_pct'].mean():+.2f}%")

# 综合推荐
print(f"\n{'=' * 70}")
print(f"  综合对比")
print(f"{'=' * 70}")
print(f"  {'持有期':<8s} {'信号数':>6s} {'胜率':>8s} {'均值':>10s} {'中位数':>10s} {'赔率':>8s}")
print(f"  {'─'*52}")

best_label = None
best_ev = -999
for label in ['40天', '60天', '80天']:
    recs = results[label]
    if not recs:
        continue
    r = np.array([x['return_pct'] for x in recs])
    N = len(r)
    wr = (r > 0).mean() * 100
    avg = np.mean(r)
    med = np.median(r)
    wins = r[r > 0]
    losses = r[r <= 0]
    odds = abs(np.mean(wins) / np.mean(losses)) if len(losses) > 0 and np.mean(losses) != 0 else 0
    print(f"  {label:<8s} {N:>6d} {wr:>7.1f}% {avg:>9.2f}% {med:>9.2f}% {odds:>7.2f}")
    
    # 期望值
    ev = 0
    if len(wins) > 0 and len(losses) > 0:
        ev = np.mean(wins) * (len(wins)/N) + np.mean(losses) * (len(losses)/N)
    if ev > best_ev:
        best_ev = ev
        best_label = label

if best_label:
    print(f"\n  🏆 最优持有期: {best_label} (期望值 {best_ev:+.2f}%)")

# 月度信号分布
print(f"\n  月度信号分布 (最近12个牛市月):")
sig_dates = sorted(set(r['date'] for r in results['60天']))
for sd in sig_dates[-12:]:
    count = sum(1 for r in results['60天'] if r['date'] == sd)
    rets = [r['return_pct'] for r in results['60天'] if r['date'] == sd]
    avg_ret = np.mean(rets) if rets else 0
    print(f"    {sd.strftime('%Y-%m')}: {count}个信号, 均值{avg_ret:+.1f}%")

print(f"\n✅ 回测完成")

# ============================================================
# 6. 保存结果
# ============================================================
output_dir = '/Users/ziruzhu/WorkBuddy/2026-07-09-21-31-33'
for label in ['40天', '60天', '80天']:
    if results[label]:
        df_out = pd.DataFrame(results[label])
        df_out.to_csv(f'{output_dir}/s003_{label}_signals.csv', index=False, encoding='utf-8-sig')

df_bench.to_csv(f'{output_dir}/s003_benchmark.csv', index=False, encoding='utf-8-sig')
print(f"\n结果已保存到: {output_dir}/")
