#!/usr/bin/env python3
"""
S003 v2 Final — 最后一版
改动:
  1. 选股池: Top300 高流动性(日均成交额)
  2. 双确认: 动量Top8 ∩ 资金流Top8 → 取前5板块
  3. 60分钟K线 + 固定240bar持有
"""
import sqlite3, pandas as pd, numpy as np, os, random

DB = os.path.expanduser('~/stock-data/stock_all.db')
random.seed(42); np.random.seed(42)

print("=" * 60)
print("S003 v2 Final — 60分钟K线 + Top300 + 双确认Top8")
print("=" * 60)

conn = sqlite3.connect(DB)

# 日线数据
print("\n[1/4] 加载数据...")
df_meta = pd.read_sql("SELECT ts_code, industry FROM stock_list WHERE industry IS NOT NULL AND industry != ''", conn)
st = pd.read_sql("SELECT ts_code FROM blacklist_st", conn)['ts_code'].tolist()
loss = pd.read_sql("SELECT ts_code FROM blacklist_loss", conn)['ts_code'].tolist()
blacklist = set(st + loss)

df_day = pd.read_sql("""
    SELECT ts_code, trade_date, CAST(close AS REAL) c
    FROM daily WHERE trade_date >= '20220101' ORDER BY ts_code, trade_date
""", conn)
df_day['trade_date'] = pd.to_datetime(df_day['trade_date'], format='%Y%m%d')
close_day = df_day.pivot(index='trade_date', columns='ts_code', values='c').sort_index()

df_mf = pd.read_sql("""
    SELECT ts_code, trade_date, CAST(net_mf_amount AS REAL) net
    FROM moneyflow WHERE trade_date >= '20220101' ORDER BY ts_code, trade_date
""", conn)
df_mf['trade_date'] = pd.to_datetime(df_mf['trade_date'], format='%Y%m%d')

# Top300 选股池 — 近一年日均成交额
top300 = pd.read_sql("""
    SELECT ts_code FROM daily WHERE trade_date >= '20250101'
    AND ts_code NOT IN (SELECT ts_code FROM blacklist_st)
    AND ts_code NOT IN (SELECT ts_code FROM blacklist_loss)
    GROUP BY ts_code ORDER BY AVG(CAST(amount AS REAL)) DESC LIMIT 300
""", conn)['ts_code'].tolist()
print(f"  Top300选股池: {len(top300)}只")

# 60分钟线 — 只加载Top300 + 黑名单过滤
codes_str = ','.join(f"'{c}'" for c in top300)
df_60 = pd.read_sql(f"""
    SELECT ts_code, trade_time,
           CAST(open AS REAL) o, CAST(close AS REAL) c,
           CAST(high AS REAL) h, CAST(low AS REAL) l,
           CAST(vol AS REAL) v, CAST(amount AS REAL) a
    FROM stk_60min WHERE ts_code IN ({codes_str}) AND trade_time >= '2022-01-01'
    ORDER BY ts_code, trade_time
""", conn)
conn.close()

df_60['trade_time'] = pd.to_datetime(df_60['trade_time'])
close_60 = df_60.pivot(index='trade_time', columns='ts_code', values='c').sort_index()
print(f"  60分钟线: {len(df_60):,}行, {df_60['ts_code'].nunique()}只")

# ============================================
print("[2/4] 择时...")
all_stocks = [c for c in close_day.columns if c not in blacklist]
trade_dates = sorted(close_day.index)
ma120 = close_day[all_stocks].rolling(120, min_periods=60).mean()
breadth = pd.Series(0.0, index=trade_dates)
for d in trade_dates:
    if d in ma120.index:
        above = (close_day.loc[d, all_stocks] > ma120.loc[d]).sum()
        total = close_day.loc[d, all_stocks].notna().sum()
        if total > 100:
            breadth[d] = above / total

monthly = []
for y in range(2022, 2027):
    for m in range(1, 13):
        md = [d for d in trade_dates if d.year == y and d.month == m]
        if md:
            monthly.append(md[-1])

ind_map = {}
for _, row in df_meta.iterrows():
    if row['ts_code'] not in blacklist:
        ind_map.setdefault(row['industry'], []).append(row['ts_code'])

# ============================================
print("[3/4] 回测...")

records = []
bench_records = []

for si, rebal_date in enumerate(monthly):
    if breadth.get(rebal_date, 0) <= 0.50:
        continue
    di = trade_dates.index(rebal_date)
    if di < 20:
        continue
    
    if si % 6 == 0:
        print(f"  进度: {rebal_date.strftime('%Y-%m')}")
    
    # 双确认Top8 — 板块
    prev_d = trade_dates[di-20]
    s_ret = {}
    for ind, codes in ind_map.items():
        vals = [100*(close_day.loc[rebal_date,c]/close_day.loc[prev_d,c]-1)
                for c in codes if c in close_day.columns and prev_d in close_day.index
                and rebal_date in close_day.index and not pd.isna(close_day.loc[prev_d,c])
                and not pd.isna(close_day.loc[rebal_date,c]) and close_day.loc[prev_d,c]>0]
        if len(vals) >= 5:
            s_ret[ind] = np.mean(vals)
    
    win_start = trade_dates[max(0, di-20)]
    mf_win = df_mf[(df_mf['trade_date']>=win_start)&(df_mf['trade_date']<=rebal_date)]
    mf_agg = mf_win.groupby('ts_code')['net'].sum()
    s_flow = {ind: sum(mf_agg.get(c,0) for c in codes if c in mf_agg.index)
              for ind, codes in ind_map.items()}
    
    # Top8 ∩ Top8 → 取前5
    flow8 = set(t[0] for t in sorted(s_flow.items(), key=lambda x: x[1], reverse=True)[:8])
    ret8 = set(t[0] for t in sorted(s_ret.items(), key=lambda x: x[1], reverse=True)[:8])
    sectors = list(flow8 & ret8)[:5]
    if not sectors:
        continue
    
    # 选股 — 只在Top300里选
    mf_dict = mf_agg.to_dict()
    picks = []
    for ind in sectors:
        scores = {}
        for c in ind_map.get(ind, []):
            if c not in top300:
                continue
            if c not in close_day.columns or pd.isna(close_day.loc[rebal_date, c]):
                continue
            ret = 100*(close_day.loc[rebal_date,c]/close_day.loc[prev_d,c]-1) if prev_d in close_day.index and not pd.isna(close_day.loc[prev_d,c]) and close_day.loc[prev_d,c]>0 else 0
            flow = mf_dict.get(c,0)/1e8
            scores[c] = flow + ret/10
        top = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:3]
        picks.extend([c for c,_ in top])
    
    if not picks:
        continue
    
    # 60分钟入场
    possible = [t for t in close_60.index if t.date() == rebal_date.date()]
    if not possible:
        possible = [t for t in close_60.index if t.date() <= rebal_date.date()]
    if not possible:
        continue
    entry_time = possible[-1]
    if entry_time not in close_60.index:
        continue
    entry_idx = close_60.index.get_loc(entry_time)
    
    for c in picks:
        if c not in close_60.columns or pd.isna(close_60.loc[entry_time, c]):
            continue
        entry_p = close_60.loc[entry_time, c]
        if entry_p <= 0:
            continue
        
        ei = min(entry_idx + 240, len(close_60)-1)
        et = close_60.index[ei]
        ep = close_60.loc[et, c] if et in close_60.index and c in close_60.columns else np.nan
        if not pd.isna(ep):
            records.append({'date': rebal_date, 'code': c, 'return_pct': 100*(ep/entry_p-1),
                           'hold_days': (et-entry_time).days})
    
    # 随机基准 — 从Top300里选
    valid_bench = [c for c in top300 if c in close_day.columns and not pd.isna(close_day.loc[rebal_date, c])]
    if len(valid_bench) >= len(picks):
        for c in random.sample(valid_bench, len(picks)):
            ed_idx = min(di+60, len(trade_dates)-1)
            ep = close_day.loc[trade_dates[ed_idx], c]
            entry = close_day.loc[rebal_date, c]
            if not pd.isna(ep) and not pd.isna(entry) and entry > 0:
                bench_records.append({'return_pct': 100*(ep/entry-1)})

# ============================================
print("[4/4] 结果\n")
print("=" * 70)

r = np.array([x['return_pct'] for x in records])
b = np.array([x['return_pct'] for x in bench_records]) if bench_records else np.array([])
N = len(r)
wr = (r > 0).mean() * 100
avg, med = np.mean(r), np.median(r)
wins, losses = r[r > 0], r[r <= 0]
odds = abs(np.mean(wins)/np.mean(losses)) if len(losses)>0 and np.mean(losses)!=0 else 0
hold = np.mean([x['hold_days'] for x in records])

print(f"  信号: {N}次 | 胜率: {wr:.1f}% | 均值: {avg:+.2f}%")
print(f"  中位: {med:+.2f}% | 赔率: {odds:.2f} | 均持: {hold:.0f}天")
print(f"  最大: {np.max(r):+.1f}% | 最小: {np.min(r):+.1f}%")
if len(b) > 0:
    print(f"  随机基准: N={len(b)} | 胜率{(b>0).mean()*100:.0f}% | 均值{np.mean(b):.2f}% | 超额{avg-np.mean(b):+.2f}%")

df_r = pd.DataFrame(records)
print(f"\n  分年:")
for y in sorted(df_r['date'].dt.year.unique()):
    yr = df_r[df_r['date'].dt.year == y]['return_pct'].values
    print(f"    {y}: {len(yr)}次 | 胜率{(yr>0).mean()*100:.0f}% | 均值{np.mean(yr):+.1f}%")

print(f"\n  分位数: P10={np.percentile(r,10):.1f}% P25={np.percentile(r,25):.1f}% P50={np.percentile(r,50):.1f}% P75={np.percentile(r,75):.1f}% P90={np.percentile(r,90):.1f}%")

print("\n✅ 完成")
