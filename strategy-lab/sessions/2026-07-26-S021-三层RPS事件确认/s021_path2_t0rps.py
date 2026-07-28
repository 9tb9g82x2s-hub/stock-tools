#!/usr/bin/env python3
"""
路径2：T0当天三层RPS实时判断 → 次日买入收益
用s021_events.csv confirm_day==0的已有三层RPS，研究T0当天象限/分位对T0次日买入收益的区分度
买入：T0次日开盘  出场：持20天/止损-12%
"""
import sqlite3, pandas as pd, numpy as np
import warnings; warnings.filterwarnings('ignore')

DB  = '/Users/ziruzhu/stock-data/stock_all.db'
CSV = '/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-26-S021-三层RPS事件确认/s021_events.csv'

print("="*68); print("路径2：T0当天三层RPS → 次日买入收益"); print("="*68, flush=True)

# ---- 取T0当天数据 ----
df  = pd.read_csv(CSV)
d0  = df[df['confirm_day'] == 0].copy()
N_ALL = len(d0)
print(f"T0事件总数: {N_ALL}", flush=True)

# ---- 加载日线 ----
codes = d0['ts_code'].unique().tolist()
conn  = sqlite3.connect(DB)
daily = pd.read_sql(
    "SELECT ts_code,trade_date,open,high,low,close FROM daily WHERE trade_date>='20160101'", conn)
conn.close()
for c in ['open','high','low','close']: daily[c] = pd.to_numeric(daily[c], errors='coerce')
daily = daily[daily['ts_code'].isin(set(codes))].sort_values(['ts_code','trade_date']).reset_index(drop=True)
dg = {c: g.reset_index(drop=True) for c, g in daily.groupby('ts_code')}
print(f"日线: {len(daily)}行", flush=True)

# ---- 算T0次日买入 20天/止损-12% 收益 ----
HOLD, SL = 20, -0.12
rows = []
for _, row in d0.iterrows():
    code = row['ts_code']; t0 = str(row['t0_date'])
    g = dg.get(code)
    if g is None: continue
    pos = g.index[g['trade_date'] == t0].tolist()
    if not pos: continue
    T = pos[0]
    if T+1+HOLD >= len(g): continue
    buy = g['open'].iloc[T+1]
    if buy <= 0 or pd.isna(buy): continue
    path = (g['close'].iloc[T+2:T+1+HOLD+1].values / buy - 1)
    if len(path) < 5: continue
    exit_d = len(path)-1
    for i,v in enumerate(path):
        if v <= SL: exit_d = i; break
    hit50  = int(np.any(g['high'].iloc[T+2:T+1+HOLD+1].values / buy - 1 >= 0.5))
    rows.append({'ts_code':code,'t0_date':t0,'actual_ret':path[exit_d],'hit50':hit50,
                 'stock_rps':row['stock_rps'],'sector_rps':row['sector_rps'],
                 'stock_rps_slope5':row['stock_rps_slope5'],
                 'rel_rps':row['rel_rps'],'quadrant':row['quadrant'],
                 'year':int(t0[:4])})

res = pd.DataFrame(rows)
BASE_RET = res['actual_ret'].mean()*100
BASE_WR  = (res['actual_ret']>0).mean()*100
print(f"有效样本: {len(res)}  基准: 均{BASE_RET:.1f}% 胜率{BASE_WR:.1f}%\n", flush=True)

def show(name, mask, base_ret=None, base_wr=None):
    s = res[mask]; n = len(s)
    if n < 50: print(f"  {name:<45} n<50"); return
    mn  = s['actual_ret'].mean()*100
    wr  = (s['actual_ret']>0).mean()*100
    h50 = s['hit50'].mean()*100
    br  = base_ret or BASE_RET; bw = base_wr or BASE_WR
    tag = ' ★' if mn>br+1 and wr>bw+2 else ''
    print(f"  {name:<45} n={n:>5}  均{mn:>6.1f}%  胜率{wr:>5.1f}%  ≥50%:{h50:>5.1f}%{tag}")

# ==== A. 四象限 ====
print("【A】T0当天四象限 → 次日收益")
for q in ['风口龙头','逆势独走','跟风补涨','双弱']:
    show(q, res['quadrant']==q)

# ==== B. stock_rps分位 ====
print("\n【B】T0当天 stock_rps 分位")
for lo,hi in [(0,50),(50,70),(70,85),(85,95),(95,100)]:
    show(f"stock_rps [{lo},{hi})", (res['stock_rps']>=lo)&(res['stock_rps']<hi))

# ==== C. 板块RPS分位 ====
print("\n【C】T0当天 sector_rps 分位")
for lo,hi in [(0,50),(50,70),(70,85),(85,95),(95,100)]:
    show(f"sector_rps [{lo},{hi})", (res['sector_rps']>=lo)&(res['sector_rps']<hi))

# ==== D. 核心组合扫描 ====
print("\n【D】核心组合扫描 (T0当天条件)")
combos = [
    ("风口龙头 + stock_rps>85", (res['quadrant']=='风口龙头')&(res['stock_rps']>85)),
    ("风口龙头 + stock_rps>90", (res['quadrant']=='风口龙头')&(res['stock_rps']>90)),
    ("风口龙头 + stock_rps>95", (res['quadrant']=='风口龙头')&(res['stock_rps']>95)),
    ("逆势独走 + stock_rps>85", (res['quadrant']=='逆势独走')&(res['stock_rps']>85)),
    ("stock_rps>90 + sector_rps>85", (res['stock_rps']>90)&(res['sector_rps']>85)),
    ("stock_rps>95 + sector_rps>90", (res['stock_rps']>95)&(res['sector_rps']>90)),
    ("stock_rps>95 + rel_rps>0",     (res['stock_rps']>95)&(res['rel_rps']>0)),
    ("stock_rps>95 + sector_rps<50 (板块弱)", (res['stock_rps']>95)&(res['sector_rps']<50)),
]
for name, mask in combos:
    show(name, mask)

# ==== E. 网格：stock_rps × sector_rps ====
print("\n【E】网格扫描 stock_rps × sector_rps → 均收益")
rps_bins = [0,70,85,95,100]
print(f"  {'stk\\sec':<12}" + "".join(f"  sec[{rps_bins[i]},{rps_bins[i+1]})" for i in range(len(rps_bins)-1)))
for j in range(len(rps_bins)-1):
    rl,rh = rps_bins[j], rps_bins[j+1]
    line  = f"  stk[{rl},{rh})"
    for i in range(len(rps_bins)-1):
        sl2,sh2 = rps_bins[i], rps_bins[i+1]
        s = res[(res['stock_rps']>=rl)&(res['stock_rps']<rh)&(res['sector_rps']>=sl2)&(res['sector_rps']<sh2)]
        if len(s)<30: line += f"  {'n<30':>12}"
        else:
            mn  = s['actual_ret'].mean()*100
            wr  = (s['actual_ret']>0).mean()*100
            line += f"  {mn:>+5.1f}%/{wr:.0f}%(n{len(s)})"[:14]
    print(line)

# ==== F. 分年（最优组合）====
best_mask = (res['stock_rps']>95) & (res['sector_rps']>90)
best = res[best_mask]
if len(best)>30:
    print(f"\n【F】分年表现 (stock_rps>95 + sector_rps>90, n={len(best)})")
    yr = best.groupby('year').agg(
        n=('actual_ret','count'),
        avg=('actual_ret', lambda x: x.mean()*100),
        wr=('actual_ret',  lambda x: (x>0).mean()*100),
        h50=('hit50','mean')
    ).reset_index()
    print(f"  {'年':<6}{'n':>5}  {'均':>7}  {'胜率':>7}  {'≥50%':>7}")
    for _,r in yr.iterrows():
        print(f"  {int(r['year']):<6}{int(r['n']):>5}  {r['avg']:>6.1f}%  {r['wr']:>6.1f}%  {r['h50']*100:>6.1f}%")

print(f"\n{'='*68}\n完成\n{'='*68}", flush=True)
