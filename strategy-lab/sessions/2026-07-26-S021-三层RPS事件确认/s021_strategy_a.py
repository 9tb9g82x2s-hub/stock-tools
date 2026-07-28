#!/usr/bin/env python3
"""
S021-A 策略特征分析：50%波段确认跟随
========================================
信号：T0后第5天确认，stock_rps>95 + 板块rps>90
买入：T5确认收盘后，T6开盘买入（用T5收盘价模拟，实际略有偏差）
出场：多档止损止盈组合下的实际收益分布

核心问题：
1. 收益分布长什么样（中位/均值/胜率/夏普）
2. 分年表现是否稳定（还是某段牛市撑起来的）
3. 不同止损设定对整体回报的影响
4. 最终：策略年化收益/胜率/最大回撤估计
"""
import sqlite3, pandas as pd, numpy as np
import warnings; warnings.filterwarnings('ignore')

DB = '/Users/ziruzhu/stock-data/stock_all.db'
CSV = '/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-26-S021-三层RPS事件确认/s021_events.csv'
OUT = '/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-26-S021-三层RPS事件确认'

# 策略参数
RPS_THR = 95
SEC_THR = 90
HOLD_DAYS = [10, 15, 20, 30]  # 测不同持仓天数
SL_PCTS = [-0.08, -0.12, -0.15]  # 止损线（-8%/-12%/-15%）

print("="*70); print("S021-A 50%波段确认策略 特征分析"); print("="*70, flush=True)

# ============ 1. 加载数据 ============
df = pd.read_csv(CSV)
d5 = df[df['confirm_day'] == 5].copy()

# 筛出信号组
mask = (d5['stock_rps'] > RPS_THR) & (d5['sector_rps'] > SEC_THR)
sig = d5[mask].copy()
print(f"信号组: n={len(sig)} (rps>{RPS_THR} + 板块rps>{SEC_THR})")
print(f"全样本: n={len(d5)}", flush=True)

# ============ 2. 加载日线（只用信号股）============
codes = sig['ts_code'].unique().tolist()
conn = sqlite3.connect(DB)
daily = pd.read_sql(
    f"SELECT ts_code,trade_date,open,high,low,close FROM daily WHERE trade_date>='20160101'", conn)
conn.close()
for c in ['open','high','low','close']: daily[c] = pd.to_numeric(daily[c], errors='coerce')
daily = daily[daily['ts_code'].isin(set(codes))].sort_values(['ts_code','trade_date']).reset_index(drop=True)
daily_g = {c: g.reset_index(drop=True) for c, g in daily.groupby('ts_code')}
print(f"日线加载: {len(daily)}行", flush=True)

# ============ 3. 逐事件计算收益路径 ============
results = []
for _, row in sig.iterrows():
    code = row['ts_code']; t0 = str(row['t0_date'])
    g = daily_g.get(code)
    if g is None: continue
    pos = g.index[g['trade_date'] == t0].tolist()
    if not pos: continue
    T = pos[0]; T5 = T + 5; T6 = T + 6
    if T6 >= len(g): continue
    # 买入价 = T6开盘（实盘）
    buy = g['open'].iloc[T6]
    if buy <= 0 or pd.isna(buy): continue
    c5 = g['close'].iloc[T5]  # 确认日收盘

    rec = {'ts_code': code, 't0_date': t0, 'buy_price': buy, 'c5': c5,
           'stock_rps': row['stock_rps'], 'sector_rps': row['sector_rps'],
           'year': int(str(t0)[:4])}

    # 持有路径（最多60天）
    path_high = []  # 每天相对买入的最高点
    path_close = []
    for d in range(1, 61):
        Ti = T6 + d
        if Ti >= len(g):
            break
        path_high.append(g['high'].iloc[Ti] / buy - 1)
        path_close.append(g['close'].iloc[Ti] / buy - 1)

    if len(path_close) < 5: continue
    path_close = np.array(path_close)
    path_high = np.array(path_high)

    # 每个持有天数 × 每个止损线的实际收益
    for hold in HOLD_DAYS:
        h = min(hold, len(path_close)) - 1
        for sl in SL_PCTS:
            # 止损：持有期内日收盘跌破sl即出
            exit_day = h
            for d_idx in range(h + 1):
                if path_close[d_idx] <= sl:
                    exit_day = d_idx; break
            actual_ret = path_close[exit_day]
            hit50 = int(np.any(path_high[:h+1] >= 0.5))
            hit100 = int(np.any(path_high[:h+1] >= 1.0))
            r = dict(rec)
            r['hold'] = hold; r['sl'] = sl
            r['actual_ret'] = actual_ret
            r['hit50'] = hit50; r['hit100'] = hit100
            r['exit_day'] = exit_day + 1
            results.append(r)

res = pd.DataFrame(results)
print(f"回测行数: {len(res)}\n", flush=True)

# ============ 4. 结果展示 ============
print("="*70)
print("【A】不同持仓天数 × 止损线的平均收益/胜率（胜率=收盘>0%）")
print("="*70)
print(f"{'持仓':<6}{'止损':<8}{'n':>6}{'均收益':>8}{'中位':>8}{'胜率%':>8}{'≥50%':>8}{'翻倍%':>8}")
print("-"*60)
for hold in HOLD_DAYS:
    for sl in SL_PCTS:
        s = res[(res['hold']==hold) & (res['sl']==sl)]
        if len(s) == 0: continue
        mn = s['actual_ret'].mean()*100
        md = s['actual_ret'].median()*100
        wr = (s['actual_ret']>0).mean()*100
        h50 = s['hit50'].mean()*100
        h100 = s['hit100'].mean()*100
        n = len(s)
        print(f"{hold:<6}{sl*100:.0f}%{'':<4}{n:>6}{mn:>7.1f}%{md:>7.1f}%{wr:>7.1f}%{h50:>7.1f}%{h100:>7.1f}%")
    print()

# ============ 5. 分年表现（用20天持仓/-12%止损作为代表） ============
print("="*70)
print("【B】分年表现（20天持仓 / -12%止损）")
print("="*70)
ref = res[(res['hold']==20) & (res['sl']==-0.12)]
yr = ref.groupby('year').agg(
    n=('actual_ret','count'),
    avg_ret=('actual_ret', lambda x: x.mean()*100),
    median_ret=('actual_ret', lambda x: x.median()*100),
    win_rate=('actual_ret', lambda x: (x>0).mean()*100),
    hit50=('hit50','mean')
).reset_index()
yr['hit50'] = yr['hit50']*100
print(f"{'年份':<6}{'n':>5}{'均收益':>8}{'中位':>8}{'胜率%':>8}{'≥50%命中%':>12}")
for _, r in yr.iterrows():
    print(f"{int(r['year']):<6}{int(r['n']):>5}{r['avg_ret']:>7.1f}%{r['median_ret']:>7.1f}%"
          f"{r['win_rate']:>7.1f}%{r['hit50']:>11.1f}%")

# ============ 6. 对照：全样本（无过滤）同参数 ============
print("\n" + "="*70)
print("【C】对照组：全样本（无过滤）同参数（20天持仓/-12%止损）")
print("="*70)
# 用所有d5事件做同样计算（只取20天/-12%）
all_res = []
for _, row in d5.iterrows():
    code = row['ts_code']; t0 = str(row['t0_date'])
    g = daily_g.get(code)
    if g is None: continue
    pos = g.index[g['trade_date'] == t0].tolist()
    if not pos: continue
    T = pos[0]; T6 = T + 6
    if T6 >= len(g): continue
    buy = g['open'].iloc[T6]
    if buy <= 0 or pd.isna(buy): continue
    path_close = []
    path_high = []
    for d in range(1, 21):
        Ti = T6 + d
        if Ti >= len(g): break
        path_close.append(g['close'].iloc[Ti] / buy - 1)
        path_high.append(g['high'].iloc[Ti] / buy - 1)
    if len(path_close) < 5: continue
    path_close = np.array(path_close)
    path_high = np.array(path_high)
    exit_day = len(path_close)-1
    for d_idx in range(len(path_close)):
        if path_close[d_idx] <= -0.12:
            exit_day = d_idx; break
    all_res.append({'actual_ret': path_close[exit_day],
                    'hit50': int(np.any(path_high >= 0.5))})

all_df = pd.DataFrame(all_res)
print(f"全样本 n={len(all_df)}")
print(f"  均收益:{all_df['actual_ret'].mean()*100:.1f}%  中位:{all_df['actual_ret'].median()*100:.1f}%  "
      f"胜率:{(all_df['actual_ret']>0).mean()*100:.1f}%  ≥50%:{all_df['hit50'].mean()*100:.1f}%")
print(f"信号组 n={len(ref)}")
print(f"  均收益:{ref['actual_ret'].mean()*100:.1f}%  中位:{ref['actual_ret'].median()*100:.1f}%  "
      f"胜率:{(ref['actual_ret']>0).mean()*100:.1f}%  ≥50%:{ref['hit50'].mean()*100:.1f}%")

print(f"\n{'='*70}\n完成\n{'='*70}", flush=True)
