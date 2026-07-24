#!/usr/bin/env python3
"""
S003 v2 全量回测 — 60分钟K线版
双确认(资金流TOP5 ∩ 动量TOP5)选板块 → 板块内资金+动量选股
对比: 固定240bar(60天) /跌破MA60止损
"""
import sqlite3, pandas as pd, numpy as np, os, sys, random

DB = os.path.expanduser('~/stock-data/stock_all.db')
random.seed(42); np.random.seed(42)

print("=" * 60)
print("S003 v2 全量回测 — 60分钟K线")
print("=" * 60)

conn = sqlite3.connect(DB)

# ============================================
# 1. 日线数据(择时+行业+双确认)
# ============================================
print("\n[1/5] 加载日线...")
df_meta = pd.read_sql("SELECT ts_code, industry FROM stock_list WHERE industry IS NOT NULL AND industry != ''", conn)
st = pd.read_sql("SELECT ts_code FROM blacklist_st", conn)['ts_code'].tolist()
loss = pd.read_sql("SELECT ts_code FROM blacklist_loss", conn)['ts_code'].tolist()
blacklist = set(st + loss)
print(f"  行业: {df_meta['industry'].nunique()}个 | 黑名单: {len(blacklist)}")

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

# ============================================
# 2. 60分钟数据
# ============================================
print("[2/5] 加载60分钟线...")
df_60 = pd.read_sql("""
    SELECT ts_code, trade_time,
           CAST(open AS REAL) o, CAST(close AS REAL) c,
           CAST(high AS REAL) h, CAST(low AS REAL) l,
           CAST(vol AS REAL) v, CAST(amount AS REAL) a
    FROM stk_60min WHERE trade_time >= '2022-01-01'
    ORDER BY ts_code, trade_time
""", conn)
conn.close()

df_60['trade_time'] = pd.to_datetime(df_60['trade_time'])
print(f"  60分钟线: {len(df_60):,}行, {df_60['ts_code'].nunique()}只")
close_60 = df_60.pivot(index='trade_time', columns='ts_code', values='c').sort_index()

# ============================================
# 3. 择时 + 均线
# ============================================
print("[3/5] 计算择时+均线...")
all_stocks = [c for c in close_day.columns if c not in blacklist]
trade_dates = sorted(close_day.index)

ma120_day = close_day[all_stocks].rolling(120, min_periods=60).mean()
breadth = pd.Series(0.0, index=trade_dates)
for d in trade_dates:
    if d in ma120_day.index:
        above = (close_day.loc[d, all_stocks] > ma120_day.loc[d]).sum()
        total = close_day.loc[d, all_stocks].notna().sum()
        if total > 100:
            breadth[d] = above / total

# 60分钟均线
ma60_60 = close_60.rolling(240).mean()  # MA60天

# 月度调仓
monthly = []
for y in range(2022, 2027):
    for m in range(1, 13):
        md = [d for d in trade_dates if d.year == y and d.month == m]
        if md:
            monthly.append(md[-1])

# 行业映射
ind_map = {}
for _, row in df_meta.iterrows():
    if row['ts_code'] not in blacklist:
        ind_map.setdefault(row['industry'], []).append(row['ts_code'])

print(f"  月度调仓: {len(monthly)}次 | 行业: {len(ind_map)}个")

# ============================================
# 4. 回测
# ============================================
print("[4/5] 回测中...")

configs = {
    '固定240bar(60天)': {'hold': 240, 'exit': None},
    '跌破MA60止损':     {'hold': None, 'exit': 'ma60'},
}

all_results = {}

for cfg_name, cfg in configs.items():
    print(f"  {cfg_name}...")
    records = []
    bench_records = []
    
    for si, rebal_date in enumerate(monthly):
        if breadth.get(rebal_date, 0) <= 0.50:
            continue
        di = trade_dates.index(rebal_date)
        if di < 20:
            continue
        
        if si % 6 == 0:
            print(f"    进度: {rebal_date.strftime('%Y-%m')}")
        
        # 双确认选板块
        prev_d = trade_dates[di - 20]
        s_ret = {}
        for ind, codes in ind_map.items():
            vals = []
            for c in codes:
                if c in close_day.columns and prev_d in close_day.index and rebal_date in close_day.index:
                    p0 = close_day.loc[prev_d, c]; p1 = close_day.loc[rebal_date, c]
                    if not pd.isna(p0) and not pd.isna(p1) and p0 > 0:
                        vals.append(100*(p1/p0-1))
            if len(vals) >= 5:
                s_ret[ind] = np.mean(vals)
        
        win_start = trade_dates[max(0, di-20)]
        mf_win = df_mf[(df_mf['trade_date']>=win_start)&(df_mf['trade_date']<=rebal_date)]
        mf_agg = mf_win.groupby('ts_code')['net'].sum()
        s_flow = {ind: sum(mf_agg.get(c,0) for c in codes if c in mf_agg.index) 
                  for ind, codes in ind_map.items()}
        
        flow5 = set(t[0] for t in sorted(s_flow.items(), key=lambda x: x[1], reverse=True)[:5])
        ret5 = set(t[0] for t in sorted(s_ret.items(), key=lambda x: x[1], reverse=True)[:5])
        sectors = list(flow5 & ret5)[:3]
        if not sectors:
            continue
        
        # 选股
        mf_dict = mf_agg.to_dict()
        picks = []
        for ind in sectors:
            scores = {}
            for c in ind_map.get(ind, []):
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
        
        # 逐只操作
        for c in picks:
            if c not in close_60.columns or pd.isna(close_60.loc[entry_time, c]):
                continue
            entry_p = close_60.loc[entry_time, c]
            if entry_p <= 0:
                continue
            
            if cfg['hold']:
                ei = min(entry_idx + cfg['hold'], len(close_60)-1)
                et = close_60.index[ei]
                ep = close_60.loc[et, c] if et in close_60.index and c in close_60.columns else np.nan
                if not pd.isna(ep):
                    records.append({'date': rebal_date, 'code': c, 'return_pct': 100*(ep/entry_p-1),
                                   'hold_days': (et-entry_time).days})
            elif cfg['exit'] == 'ma60':
                for i in range(1, 1000):
                    ei = entry_idx + i
                    if ei >= len(close_60):
                        break
                    t = close_60.index[ei]
                    cp = close_60.loc[t, c] if t in close_60.index else np.nan
                    m60 = ma60_60.loc[t, c] if t in ma60_60.index and c in ma60_60.columns else np.nan
                    if not pd.isna(cp) and not pd.isna(m60) and cp < m60:
                        ep = close_60.loc[t, c] if c in close_60.columns else np.nan
                        if not pd.isna(ep):
                            records.append({'date': rebal_date, 'code': c, 'return_pct': 100*(ep/entry_p-1),
                                           'hold_days': (t-entry_time).days})
                        break
                else:
                    ei = min(entry_idx + 1000, len(close_60)-1)
                    t = close_60.index[ei]
                    ep = close_60.loc[t, c] if c in close_60.columns else np.nan
                    if not pd.isna(ep):
                        records.append({'date': rebal_date, 'code': c, 'return_pct': 100*(ep/entry_p-1),
                                       'hold_days': (t-entry_time).days})
        
        # 随机基准
        valid = [c for c in all_stocks if c in close_day.columns and not pd.isna(close_day.loc[rebal_date, c])]
        if len(valid) >= len(picks):
            for c in random.sample(valid, len(picks)):
                ep_d = close_day.loc[trade_dates[min(di+60, len(trade_dates)-1)], c] if min(di+60, len(trade_dates)-1) < len(trade_dates) else np.nan
                entry = close_day.loc[rebal_date, c]
                if not pd.isna(ep_d) and not pd.isna(entry) and entry > 0:
                    bench_records.append({'return_pct': 100*(ep_d/entry-1)})
    
    all_results[cfg_name] = {'records': records, 'bench': bench_records}

# ============================================
# 5. 结果
# ============================================
print("\n[5/5] 汇总...\n")
print("=" * 75)
print("  S003 v2 全量回测  —  60分钟K线")
print("=" * 75)

for name, data in all_results.items():
    recs = data['records']
    if not recs:
        print(f"\n  {name}: 无信号")
        continue
    
    r = np.array([x['return_pct'] for x in recs])
    b = np.array([x['return_pct'] for x in data['bench']])
    N = len(r)
    wr = (r > 0).mean() * 100
    avg, med = np.mean(r), np.median(r)
    wins, losses = r[r > 0], r[r <= 0]
    odds = abs(np.mean(wins)/np.mean(losses)) if len(losses)>0 and np.mean(losses)!=0 else 0
    hold = np.mean([x['hold_days'] for x in recs])
    excess = avg - np.mean(b) if len(b) > 0 else 0
    
    print(f"\n{'─' * 75}")
    print(f"  {name}")
    print(f"{'─' * 75}")
    print(f"  信号: {N}次 | 胜率: {wr:.1f}% | 均值: {avg:+.2f}% | 中位: {med:+.2f}%")
    print(f"  赔率: {odds:.2f}:1 | 均持: {hold:.0f}天 | 最大: {np.max(r):+.1f}% | 最小: {np.min(r):+.1f}%")
    if len(b) > 0:
        print(f"  随机基准: 胜率{(b>0).mean()*100:.0f}% | 均值{np.mean(b):.2f}% | 超额{excess:+.2f}%")
    
    df_r = pd.DataFrame(recs)
    if 'date' in df_r.columns:
        print(f"\n  分年:")
        for y in sorted(df_r['date'].dt.year.unique()):
            yr = df_r[df_r['date'].dt.year == y]['return_pct'].values
            print(f"    {y}: {len(yr)}次 | 胜率{(yr>0).mean()*100:.0f}% | 均值{np.mean(yr):+.1f}%")

# 综合对比
print(f"\n{'=' * 75}")
print(f"  综合对比")
print(f"{'=' * 75}")
print(f"  {'方案':<22s} {'交易':>6s} {'胜率':>8s} {'均值':>10s} {'赔率':>8s} {'超额':>8s}")
print(f"  {'─'*64}")
for name, data in all_results.items():
    recs = data['records']
    if not recs:
        continue
    r = np.array([x['return_pct'] for x in recs])
    b = np.array([x['return_pct'] for x in data['bench']]) if data['bench'] else np.array([])
    N = len(r)
    wr = (r>0).mean()*100
    avg = np.mean(r)
    wins, losses = r[r>0], r[r<=0]
    odds = abs(np.mean(wins)/np.mean(losses)) if len(losses)>0 and np.mean(losses)!=0 else 0
    excess = avg - np.mean(b) if len(b)>0 else 0
    print(f"  {name:<22s} {N:>6d} {wr:>7.1f}% {avg:>9.2f}% {odds:>7.2f} {excess:>+7.2f}%")

print("\n✅ 回测完成")
