#!/usr/bin/env python3
"""
S021 三层RPS + 斜率背离 + 事件确认分析
=======================================
范式转变：从S020的"预测"(起点前1天特征,无区分力) → "确认"(启动后3-5天三层RPS结构)

核心问题：
  翻倍股在【启动后3-5天】,能否用三层RPS结构区分"真翻倍"vs"假突破"?
  哪个象限(板块强弱×个股强弱)占比最高? 确认信号提前量多少?

三层RPS：
  大盘(基准)  = 全市场等权累计指数
  板块RPS     = 概念指数N日涨幅在所有概念中的百分位(用官方ths_daily概念指数)
  个股RPS     = 个股N日涨幅在全市场的百分位
  相对RPS     = 个股RPS - 主导概念板块RPS (个股相对板块的超额)

事件定义：
  T0(启动日) = 某日涨幅>7% 或 放量突破20日高点
  真翻倍     = T0后[FWD_MIN,FWD_MAX]内最高价/T0收盘 >= 2.0 (label=1)
  50%对照    = >= 1.5 (label=0.5, 单独分析)
  假动作     = < 1.5 (label=0)

主导概念：该股所属所有概念中,T0时板块RPS最高者(风口龙头视角)
"""
import sqlite3, pandas as pd, numpy as np, time, os
import warnings; warnings.filterwarnings('ignore')

DB = "/Users/ziruzhu/stock-data/stock_all.db"
OUT = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-26-S021-三层RPS事件确认"
START, END = "20160101", "20260724"
LOOKBACK = 25          # 启动前回看(定义突破用)
FWD_MIN, FWD_MAX = 10, 60   # 翻倍观察窗
DOUBLE_THR, HALF_THR = 2.0, 1.5
CONFIRM_DAYS = [1, 3, 5]    # 确认观察点(T0后第N天)
RPS_N = 20             # RPS的动量窗口(20日涨幅排名)

t0 = time.time()
print("="*64); print("S021 三层RPS事件确认分析"); print("="*64, flush=True)

conn = sqlite3.connect(DB)

# ============ 股票池(铁律:剔北交所+ST+亏损) ============
codes = pd.read_sql("SELECT DISTINCT ts_code FROM daily", conn)['ts_code'].tolist()
codes = [c for c in codes if not c.endswith('.BJ')]
bl = set(pd.read_sql("SELECT ts_code FROM blacklist_st", conn)['ts_code']) | \
     set(pd.read_sql("SELECT ts_code FROM blacklist_loss", conn)['ts_code'])
codes = [c for c in codes if c not in bl]
codeset = set(codes)
print(f"股票池: {len(codes)} 只", flush=True)

# ============ 加载个股日线 ============
print("加载daily...", flush=True)
daily = pd.read_sql(
    f"SELECT ts_code,trade_date,open,high,low,close,vol,pct_chg FROM daily "
    f"WHERE trade_date>='{START}' AND trade_date<='{END}'", conn)
for c in ['open','high','low','close','vol','pct_chg']:
    daily[c] = pd.to_numeric(daily[c], errors='coerce')
daily = daily[daily['ts_code'].isin(codeset)].dropna(subset=['close','high','low','open']).query('close>0')
daily = daily.sort_values(['ts_code','trade_date']).reset_index(drop=True)

# 个股RPS(全市场20日涨幅排名)
daily['ret_rps'] = daily.groupby('ts_code')['close'].transform(lambda x: x/x.shift(RPS_N)-1)
daily['stock_rps'] = daily.groupby('trade_date')['ret_rps'].rank(pct=True) * 100
print(f"  daily {len(daily)}行, 个股RPS完成", flush=True)

# ============ 大盘基准(全市场等权累计指数) ============
mkt = daily.groupby('trade_date')['pct_chg'].mean().reset_index()
mkt.columns = ['trade_date','mkt_ret']
mkt['mkt_idx'] = (1 + mkt['mkt_ret']/100).cumprod()
print(f"  大盘基准完成 {len(mkt)}个交易日", flush=True)

# ============ 概念层(板块RPS) ============
print("加载概念数据 ths_daily / ths_member...", flush=True)
# 只用N类题材概念(炒作主线),排除I行业分类 —— HANDOVER最认同的点
concept_meta = pd.read_sql(
    "SELECT ts_code,name,type FROM concept_index WHERE exchange='A' AND type='N'", conn)
concept_codes = set(concept_meta['ts_code'])
code2cname = dict(zip(concept_meta['ts_code'], concept_meta['name']))
print(f"  N类题材概念: {len(concept_codes)}个", flush=True)

# 概念指数日线
cd = pd.read_sql(
    f"SELECT ts_code,trade_date,close,pct_change FROM ths_daily "
    f"WHERE trade_date>='{START}' AND trade_date<='{END}'", conn)
cd = cd[cd['ts_code'].isin(concept_codes)].copy()
cd['close'] = pd.to_numeric(cd['close'], errors='coerce')
cd = cd.dropna(subset=['close']).query('close>0').sort_values(['ts_code','trade_date'])
# 板块RPS(概念指数20日涨幅在所有概念中排名)
cd['cret'] = cd.groupby('ts_code')['close'].transform(lambda x: x/x.shift(RPS_N)-1)
cd['sector_rps'] = cd.groupby('trade_date')['cret'].rank(pct=True) * 100
# 板块RPS斜率(5日变化)
cd['sector_rps_slope5'] = cd.groupby('ts_code')['sector_rps'].diff(5)
sector_idx = cd.set_index(['ts_code','trade_date'])[['sector_rps','sector_rps_slope5','cret']]
print(f"  概念指数 {len(cd)}行, 板块RPS完成", flush=True)

# 个股↔概念映射(只留N类题材)
mem = pd.read_sql("SELECT ts_code,con_code FROM ths_member", conn)
mem = mem[mem['ts_code'].isin(concept_codes)]  # ts_code=概念, con_code=成分股
stock2concepts = mem.groupby('con_code')['ts_code'].apply(list).to_dict()
print(f"  成分映射: {len(stock2concepts)}只股票有概念归属", flush=True)
conn.close()

# ============ 个股RPS索引(供快速查找) ============
stock_rps_idx = daily.set_index(['ts_code','trade_date'])['stock_rps']
# 个股RPS斜率
daily['stock_rps_slope5'] = daily.groupby('ts_code')['stock_rps'].diff(5)
stock_slope_idx = daily.set_index(['ts_code','trade_date'])['stock_rps_slope5']
daily_g = {c: g.reset_index(drop=True) for c, g in daily.groupby('ts_code')}
print(f"数据加载完成, 耗时{time.time()-t0:.0f}秒\n", flush=True)

# ============ 事件定位: 找启动日T0 ============
def find_t0_events(g):
    """返回启动日索引列表。启动=涨幅>7% 或 放量突破20日高点。去重:60日内同股只取首个"""
    close = g['close'].values; high = g['high'].values; vol = g['vol'].values
    pct = g['pct_chg'].values; n = len(g); events = []; last = -999
    for T in range(LOOKBACK, n - FWD_MIN):
        if T - last < FWD_MIN: continue  # 同股事件最小间隔
        h20 = high[T-LOOKBACK:T].max()
        v20 = vol[T-LOOKBACK:T].mean()
        cond_pct = pct[T] > 7
        cond_break = (high[T] > h20) and (vol[T] > 1.5 * v20)
        if cond_pct or cond_break:
            events.append(T); last = T
    return events

def label_outcome(g, T):
    """T0后[FWD_MIN,FWD_MAX]最高涨幅 → 分类"""
    close = g['close'].values; high = g['high'].values; n = len(g)
    c0 = close[T]; e = min(T + FWD_MAX, n - 1)
    if T + FWD_MIN > e: return None, None
    wh = high[T+FWD_MIN:e+1]
    if len(wh) == 0: return None, None
    mx = wh.max() / c0
    if mx >= DOUBLE_THR: return 2, mx      # 翻倍
    elif mx >= HALF_THR: return 1, mx      # 50%对照
    else: return 0, mx                     # 假动作

def three_layer_rps(code, td):
    """在td这天算三层RPS: 个股RPS, 主导板块RPS, 相对RPS(个股-板块)"""
    s_rps = stock_rps_idx.get((code, td), np.nan)
    s_slope = stock_slope_idx.get((code, td), np.nan)
    # 该股所属概念中,当天板块RPS最高的作为"主导概念"
    concepts = stock2concepts.get(code, [])
    best_sec_rps, best_sec_slope, best_cname = np.nan, np.nan, None
    for cc in concepts:
        try:
            row = sector_idx.loc[(cc, td)]
            sr = row['sector_rps']
            if pd.notna(sr) and (pd.isna(best_sec_rps) or sr > best_sec_rps):
                best_sec_rps = sr; best_sec_slope = row['sector_rps_slope5']; best_cname = cc
        except KeyError:
            continue
    rel_rps = s_rps - best_sec_rps if pd.notna(s_rps) and pd.notna(best_sec_rps) else np.nan
    return {
        'stock_rps': s_rps, 'stock_rps_slope5': s_slope,
        'sector_rps': best_sec_rps, 'sector_rps_slope5': best_sec_slope,
        'rel_rps': rel_rps, 'lead_concept': code2cname.get(best_cname, None),
        'n_concepts': len(concepts),
    }

def quadrant(sec_rps, stock_rps, thr=50):
    """四象限归因"""
    if pd.isna(sec_rps) or pd.isna(stock_rps): return 'NA'
    s = 'S强' if sec_rps >= thr else 'S弱'
    k = 'K强' if stock_rps >= thr else 'K弱'
    if s == 'S强' and k == 'K强': return '风口龙头'
    if s == 'S弱' and k == 'K强': return '逆势独走'
    if s == 'S强' and k == 'K弱': return '跟风补涨'
    return '双弱'

# ============ 遍历所有启动事件, 在确认点采样三层RPS ============
print("定位启动事件 + 采样确认点三层RPS...", flush=True)
rows = []
n_stock = 0
for code, g in daily_g.items():
    if len(g) < LOOKBACK + FWD_MAX + max(CONFIRM_DAYS) + 2: continue
    if code not in stock2concepts: continue  # 无概念归属跳过
    n_stock += 1
    dates = g['trade_date'].values
    close = g['close'].values
    for T in find_t0_events(g):
        cls, mx = label_outcome(g, T)
        if cls is None: continue
        base = {'ts_code': code, 't0_date': str(dates[T]), 'label': cls, 'max_ret': mx}
        # 在T0及T0后第1/3/5天分别采样三层RPS
        for d in [0] + CONFIRM_DAYS:
            Ti = T + d
            if Ti >= len(g): break
            td = str(dates[Ti])
            rps = three_layer_rps(code, td)
            rec = dict(base)
            rec['confirm_day'] = d
            rec.update(rps)
            rec['quadrant'] = quadrant(rps['sector_rps'], rps['stock_rps'])
            rows.append(rec)

df = pd.DataFrame(rows)
os.makedirs(OUT, exist_ok=True)
df.to_csv(f"{OUT}/s021_events.csv", index=False)
print(f"  覆盖{n_stock}只有概念的股票, 事件样本{df['t0_date'].nunique() if len(df) else 0}个, 采样行{len(df)}", flush=True)

# ============ 分析1: 四象限分布(按确认天数×结果) ============
print(f"\n{'='*64}\n【分析1】四象限归因 —— 哪类翻倍占比最高?\n{'='*64}", flush=True)
label_name = {2: '翻倍', 1: '50%对照', 0: '假动作'}
for d in CONFIRM_DAYS:
    sub = df[df['confirm_day'] == d]
    print(f"\n--- T0后第{d}天 ---")
    for lb in [2, 1, 0]:
        s = sub[sub['label'] == lb]
        if len(s) == 0: continue
        vc = s['quadrant'].value_counts(normalize=True) * 100
        dist = '  '.join(f"{k}:{v:.0f}%" for k, v in vc.items() if k != 'NA')
        print(f"  {label_name[lb]:<7}(n={len(s):>4}): {dist}")

# ============ 分析2: KS区分度(翻倍 vs 假动作) ============
print(f"\n{'='*64}\n【分析2】三层RPS指标区分度 (翻倍 vs 假动作, KS越大越强)\n{'='*64}", flush=True)
from scipy.stats import ks_2samp
feat_cols = ['stock_rps','stock_rps_slope5','sector_rps','sector_rps_slope5','rel_rps','n_concepts']
for d in CONFIRM_DAYS:
    sub = df[df['confirm_day'] == d]
    pos = sub[sub['label'] == 2]; neg = sub[sub['label'] == 0]
    print(f"\n--- T0后第{d}天 (翻倍n={len(pos)} vs 假动作n={len(neg)}) ---")
    print(f"  {'指标':<20}{'翻倍中位':>10}{'假动作中位':>12}{'KS':>8}")
    res = []
    for col in feat_cols:
        p = pos[col].dropna(); n = neg[col].dropna()
        if len(p) < 20 or len(n) < 20: continue
        ks = ks_2samp(p, n).statistic
        res.append((col, p.median(), n.median(), ks))
    for col, pm, nm, ks in sorted(res, key=lambda x: -x[3]):
        flag = ' ★' if ks > 0.15 else ''
        print(f"  {col:<20}{pm:>10.1f}{nm:>12.1f}{ks:>8.3f}{flag}")

# ============ 分析3: 确认信号提前量 ============
print(f"\n{'='*64}\n【分析3】'逆势独走'象限的翻倍捕获 (板块弱+个股强,最可能外生事件)\n{'='*64}", flush=True)
for d in CONFIRM_DAYS:
    sub = df[df['confirm_day'] == d]
    solo = sub[sub['quadrant'] == '逆势独走']
    if len(solo) == 0: continue
    hit = (solo['label'] == 2).sum(); tot = len(solo)
    lead = sub[sub['quadrant'] == '风口龙头']
    hit2 = (lead['label'] == 2).sum(); tot2 = len(lead)
    print(f"  第{d}天: 逆势独走翻倍率 {hit}/{tot}={hit/tot*100:.1f}%  |  "
          f"风口龙头翻倍率 {hit2}/{tot2}={hit2/tot2*100 if tot2 else 0:.1f}%")

# 全局基准翻倍率
base_rate = (df[df['confirm_day']==CONFIRM_DAYS[0]]['label'] == 2).mean() * 100
print(f"\n  全事件基准翻倍率: {base_rate:.1f}%  (对照:任一象限跑赢此值才有确认价值)")

print(f"\n{'='*64}")
print(f"全部完成, 耗时{(time.time()-t0)/60:.1f}分钟")
print(f"产出: {OUT}/s021_events.csv")
print(f"{'='*64}", flush=True)
