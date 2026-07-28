#!/usr/bin/env python3
"""
路径1：T0信号质量研究 —— 什么样的T0后续20天最好？
特征维度：
  A. 缩量/放量突破（vol vs vol_ma20）
  B. T0前的价格位置（ma5/ma20/布林带位置）
  C. T0当天OBV变化
  D. 触发类型：纯涨幅>7% vs 放量突破20日高
  E. 三层RPS（T0当天）
买入：T0次日开盘，持20天，止损-12%
"""
import sqlite3, pandas as pd, numpy as np
import warnings; warnings.filterwarnings('ignore')

DB  = '/Users/ziruzhu/stock-data/stock_all.db'
CSV = '/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-26-S021-三层RPS事件确认/s021_events.csv'
LOOKBACK = 25
HOLD, SL = 20, -0.12

print("="*68); print("路径1：T0信号质量研究"); print("="*68, flush=True)

df = pd.read_csv(CSV)
d0 = df[df['confirm_day'] == 0].copy()

# ---- 加载日线（全量） ----
conn  = sqlite3.connect(DB)
daily = pd.read_sql(
    "SELECT ts_code,trade_date,open,high,low,close,vol,pct_chg FROM daily WHERE trade_date>='20150601'", conn)
conn.close()
for c in ['open','high','low','close','vol','pct_chg']: daily[c] = pd.to_numeric(daily[c], errors='coerce')
codes = set(d0['ts_code'].unique())
daily = daily[daily['ts_code'].isin(codes)].sort_values(['ts_code','trade_date']).reset_index(drop=True)
dg = {c: g.reset_index(drop=True) for c, g in daily.groupby('ts_code')}
print(f"日线: {len(daily)}行", flush=True)

# ---- 逐事件提取T0特征 + 算收益 ----
rows = []
for _, row in d0.iterrows():
    code = row['ts_code']; t0 = str(row['t0_date'])
    g = dg.get(code)
    if g is None: continue
    pos = g.index[g['trade_date'] == t0].tolist()
    if not pos: continue
    T = pos[0]
    if T < LOOKBACK or T+1+HOLD >= len(g): continue

    close = g['close'].values; high = g['high'].values
    low   = g['low'].values;   vol  = g['vol'].values
    pct   = g['pct_chg'].values

    c0   = close[T]; buy = g['open'].iloc[T+1]
    if buy <= 0 or pd.isna(buy) or c0 <= 0: continue

    # 收益路径
    path = (g['close'].iloc[T+2:T+1+HOLD+1].values / buy - 1)
    if len(path) < 5: continue
    exit_d = len(path)-1
    for i,v in enumerate(path):
        if v <= SL: exit_d = i; break
    actual_ret = path[exit_d]
    hit50 = int(np.any(g['high'].iloc[T+2:T+1+HOLD+1].values / buy - 1 >= 0.5))

    # ---- T0特征 ----
    vol_ma20 = vol[T-20:T].mean()
    vol_ratio = vol[T] / vol_ma20 if vol_ma20 > 0 else np.nan  # 放量倍数

    h20 = high[T-LOOKBACK:T].max()
    cond_pct   = pct[T] > 7
    cond_break = (high[T] > h20) and (vol[T] > 1.5 * vol_ma20)
    if cond_pct and not cond_break:   trigger = 'pure_pct'
    elif cond_break and not cond_pct: trigger = 'pure_break'
    else:                             trigger = 'both'

    ma5  = close[T-5:T].mean();  ma20 = close[T-20:T].mean()
    price_vs_ma5  = c0/ma5  - 1 if ma5  > 0 else np.nan
    price_vs_ma20 = c0/ma20 - 1 if ma20 > 0 else np.nan

    # 布林带位置（20日均线±2σ）
    std20 = close[T-20:T].std()
    boll_pos = (c0 - ma20) / (2*std20) if std20 > 0 else np.nan  # >1=突破上轨

    # T0前20日回撤深度（做底的程度）
    win20 = close[T-20:T]
    dd20  = (win20.min() / win20.max() - 1) if win20.max() > 0 else np.nan

    # T0当日OBV变量（量价同向强度）
    sign_t0  = 1 if pct[T] > 0 else -1
    obv_intensity = sign_t0 * vol[T] / vol_ma20 if vol_ma20 > 0 else np.nan

    rows.append({
        'ts_code': code, 't0_date': t0,
        'actual_ret': actual_ret, 'hit50': hit50,
        'stock_rps': row['stock_rps'], 'sector_rps': row['sector_rps'],
        'vol_ratio': vol_ratio, 'trigger': trigger,
        'price_vs_ma5': price_vs_ma5, 'price_vs_ma20': price_vs_ma20,
        'boll_pos': boll_pos, 'dd20': dd20, 'obv_intensity': obv_intensity,
        'pct_t0': pct[T], 'year': int(t0[:4]),
    })

res = pd.DataFrame(rows)
BASE_RET = res['actual_ret'].mean()*100
BASE_WR  = (res['actual_ret']>0).mean()*100
print(f"有效样本: {len(res)}  基准: 均{BASE_RET:.1f}% 胜率{BASE_WR:.1f}%\n", flush=True)

def show(name, mask):
    s = res[mask]; n = len(s)
    if n < 50: print(f"  {name:<50} n<50"); return
    mn  = s['actual_ret'].mean()*100; wr = (s['actual_ret']>0).mean()*100
    h50 = s['hit50'].mean()*100
    tag = ' ★' if mn > BASE_RET+1 and wr > BASE_WR+3 else ''
    print(f"  {name:<50} n={n:>5}  均{mn:>+6.1f}%  胜率{wr:>5.1f}%  ≥50%:{h50:>5.1f}%{tag}")

from scipy.stats import ks_2samp

# ==== A. 触发类型 ====
print("【A】触发类型（纯涨>7% / 纯放量突破 / 两者同时）")
for t in ['pure_pct','pure_break','both']:
    show(t, res['trigger']==t)

# ==== B. 放量倍数 ====
print("\n【B】T0放量倍数")
for lo,hi in [(0,1),(1,1.5),(1.5,2.5),(2.5,4),(4,99)]:
    show(f"vol_ratio [{lo},{hi})", (res['vol_ratio']>=lo)&(res['vol_ratio']<hi))

# ==== C. 涨幅幅度（T0当天pct_chg） ====
print("\n【C】T0涨幅幅度")
for lo,hi in [(0,7),(7,9),(9,12),(12,20),(20,99)]:
    show(f"pct_t0 [{lo},{hi}%)", (res['pct_t0']>=lo)&(res['pct_t0']<hi))

# ==== D. T0前价格位置 ====
print("\n【D】T0前价格位置（相对20日均线偏离）")
for lo,hi in [(-99,-0.2),(-0.2,-0.05),(-0.05,0.05),(0.05,0.2),(0.2,99)]:
    show(f"price_vs_ma20 [{lo:.2f},{hi:.2f})", (res['price_vs_ma20']>=lo)&(res['price_vs_ma20']<hi))

# ==== E. T0前20日最大回撤（是否从底部启动） ====
print("\n【E】T0前20日最大回撤（越负=调整越深=越接近底部）")
for lo,hi in [(-99,-0.2),(-0.2,-0.12),(-0.12,-0.06),(-0.06,0)]:
    show(f"dd20 [{lo:.2f},{hi:.2f})", (res['dd20']>=lo)&(res['dd20']<hi))

# ==== F. 布林带位置 ====
print("\n【F】T0收盘在布林带位置（>1=突破上轨,<-1=跌破下轨）")
for lo,hi in [(-99,-1),(-1,0),(0,1),(1,2),(2,99)]:
    show(f"boll_pos [{lo},{hi})", (res['boll_pos']>=lo)&(res['boll_pos']<hi))

# ==== G. 三层RPS（T0当天） ====
print("\n【G】T0当天 stock_rps × sector_rps 核心组合")
combos = [
    ("stock_rps>95 + sector_rps>90",   (res['stock_rps']>95)&(res['sector_rps']>90)),
    ("stock_rps>90 + sector_rps>85",   (res['stock_rps']>90)&(res['sector_rps']>85)),
    ("stock_rps>85 + sector_rps>80",   (res['stock_rps']>85)&(res['sector_rps']>80)),
    ("stock_rps<50（T0时个股偏弱）",    res['stock_rps']<50),
    ("sector_rps<50（T0时板块偏弱）",   res['sector_rps']<50),
]
for name, mask in combos: show(name, mask)

# ==== H. 最优多维组合 ====
print("\n【H】最优多维组合（综合收益最高）")
combos2 = [
    ("pure_break + vol>2 + rps>85 + sec>80",
     (res['trigger']!='pure_pct')&(res['vol_ratio']>2)&(res['stock_rps']>85)&(res['sector_rps']>80)),
    ("pure_break + vol>1.5 + rps>90",
     (res['trigger']!='pure_pct')&(res['vol_ratio']>1.5)&(res['stock_rps']>90)),
    ("vol>2 + rps>90 + boll>0.5",
     (res['vol_ratio']>2)&(res['stock_rps']>90)&(res['boll_pos']>0.5)),
    ("vol>1.5 + rps>85 + dd20<-0.06（从底反弹）",
     (res['vol_ratio']>1.5)&(res['stock_rps']>85)&(res['dd20']<-0.06)),
    ("rps>95 + sec>90 + vol>1.5",
     (res['stock_rps']>95)&(res['sector_rps']>90)&(res['vol_ratio']>1.5)),
    ("rps>95 + sec>90 + vol>2 + boll>0",
     (res['stock_rps']>95)&(res['sector_rps']>90)&(res['vol_ratio']>2)&(res['boll_pos']>0)),
]
for name, mask in combos2: show(name, mask)

# ==== I. KS区分度（哪个特征最有用）====
print("\n【I】特征KS区分度（翻倍组 vs 非翻倍组，T0次日买 胜率>0%为正）")
pos = res[res['actual_ret']>0]; neg = res[res['actual_ret']<=0]
feat_cols = ['stock_rps','sector_rps','vol_ratio','pct_t0','price_vs_ma20','dd20','boll_pos','obv_intensity']
ks_rows = []
for col in feat_cols:
    p = pos[col].dropna(); n = neg[col].dropna()
    if len(p)<50 or len(n)<50: continue
    ks = ks_2samp(p,n).statistic
    ks_rows.append((col, p.median(), n.median(), ks))
print(f"  {'特征':<22}{'正收益中位':>12}{'负收益中位':>12}{'KS':>8}")
for col,pm,nm,ks in sorted(ks_rows, key=lambda x:-x[3]):
    flag = ' ★' if ks>0.08 else ''
    print(f"  {col:<22}{pm:>12.2f}{nm:>12.2f}{ks:>8.3f}{flag}")

print(f"\n{'='*68}\n完成\n{'='*68}", flush=True)
