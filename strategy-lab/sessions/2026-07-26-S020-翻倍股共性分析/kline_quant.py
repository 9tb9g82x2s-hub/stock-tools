"""
K线形态量化对比:翻倍组(A猛+B随机) vs 超跌未翻倍对照组(C)
关键:C组是"同样超跌但没翻倍"的票,与翻倍组对比才能找出真正区分信号
形态特征(起点前后):
  1. 量能:起点前地量程度、起点后放量倍数、启动日量比
  2. 下跌姿态:起点前跌幅、下跌斜率、深V vs 阴跌(单日最大跌幅/累计跌幅)
  3. 均线:起点均线粘合度、起点后是否快速上穿
  4. 启动K线:起点后N天首个涨停/大阳出现的时间和幅度
  5. 位置:起点前触及的阶段最低点后反弹力度
输出:各特征三组均值/中位+翻倍组vs对照组KS区分度
"""
import sqlite3,pandas as pd,numpy as np,time
from scipy.stats import ks_2samp
import warnings; warnings.filterwarnings('ignore')

DB="/Users/ziruzhu/stock-data/stock_all.db"
OUT="/Users/ziruzhu/stock-tools/strategy-lab/sessions/S020"
LOOKBACK=30; FWD_MIN,FWD_MAX=10,60; DOUBLE_THR=2.0; SEARCH_WIN=5
np.random.seed(42)

t0=time.time()
print("加载数据...")
conn=sqlite3.connect(DB)
codes=pd.read_sql("SELECT DISTINCT ts_code FROM daily",conn)['ts_code'].tolist()
codes=[c for c in codes if not c.endswith('.BJ')]
bl=set(pd.read_sql("SELECT ts_code FROM blacklist_st",conn)['ts_code'])|set(pd.read_sql("SELECT ts_code FROM blacklist_loss",conn)['ts_code'])
codes=[c for c in codes if c not in bl]
daily=pd.read_sql("SELECT ts_code,trade_date,open,high,low,close,vol,amount FROM daily WHERE trade_date>='20160101' AND trade_date<='20260630' ORDER BY ts_code,trade_date",conn)
for c in ['open','high','low','close','vol','amount']: daily[c]=pd.to_numeric(daily[c],errors='coerce')
daily=daily[daily['ts_code'].isin(set(codes))].dropna(subset=['open','high','low','close']).query('close>0 and low>0')
conn.close()
daily_g={c:g.reset_index(drop=True) for c,g in daily.groupby('ts_code')}
print(f"股票:{len(daily_g)},耗时{time.time()-t0:.0f}秒")

def kline_features(g,T):
    """起点T的K线形态特征"""
    i=T-1  # 起点前1天
    if i<LOOKBACK or T+10>=len(g): return None
    close=g['close'].values;high=g['high'].values;low=g['low'].values;openp=g['open'].values;vol=g['vol'].values
    c0=close[i];f={}
    # === 量能 ===
    v_pre5=vol[i-4:i+1].mean();v_pre20=vol[i-19:i+1].mean();v_pre60=vol[max(0,i-59):i+1].mean()
    f['vol_shrink_5_20']=v_pre5/v_pre20 if v_pre20>0 else np.nan  # 起点前地量程度(<1=缩量)
    f['vol_shrink_5_60']=v_pre5/v_pre60 if v_pre60>0 else np.nan
    # 起点后5天放量倍数
    v_post5=vol[T:T+5].mean()
    f['vol_expand_post5']=v_post5/v_pre20 if v_pre20>0 else np.nan
    # 起点后10天最大单日量比
    f['vol_max_post10']=vol[T:T+10].max()/v_pre20 if v_pre20>0 else np.nan
    # === 下跌姿态 ===
    f['ret_pre20']=c0/close[i-19]-1 if close[i-19]>0 else np.nan  # 前20日跌幅
    f['ret_pre60']=c0/close[max(0,i-59)]-1 if close[max(0,i-59)]>0 else np.nan
    # 下跌集中度:前20日单日最大跌幅 / 累计跌幅(越大=急跌深V,越小=阴跌)
    daily_ret=np.diff(close[i-20:i+1])/close[i-20:i]
    max_drop=abs(daily_ret.min()) if len(daily_ret)>0 else np.nan
    cum_drop=abs(min(0,c0/close[i-19]-1)) if close[i-19]>0 else np.nan
    f['drop_concentration']=max_drop/cum_drop if cum_drop and cum_drop>0 else np.nan
    # 下跌天数占比(前20天里阴线比例)
    f['down_days_ratio']=(daily_ret<0).mean() if len(daily_ret)>0 else np.nan
    # === 均线 ===
    ma5=close[i-4:i+1].mean();ma10=close[i-9:i+1].mean();ma20=close[i-19:i+1].mean()
    f['ma_converge']=np.std([ma5,ma10,ma20])/c0 if c0>0 else np.nan  # 粘合度(越小越粘合)
    f['close_below_ma20']=c0/ma20-1 if ma20>0 else np.nan  # 偏离20日线程度
    # === 启动K线 ===
    # 起点后10天内首个涨停(>9.8%)或大阳(>7%)出现的位置和幅度
    post_ret=(close[T:T+10]-close[T-1:T+9])/close[T-1:T+9]
    big_up=np.where(post_ret>0.07)[0]
    f['first_bigup_day']=int(big_up[0])+1 if len(big_up)>0 else 99  # 第几天出现大阳(99=没有)
    f['max_up_post10']=post_ret.max() if len(post_ret)>0 else np.nan  # 后10天最大单日涨幅
    # 起点当天到后3天涨幅(启动初速度)
    f['ret_post3']=close[min(T+2,len(g)-1)]/c0-1 if c0>0 else np.nan
    # === 影线(起点前5天) ===
    # 下影线比例(探底回升信号):(min(o,c)-low)/(high-low)
    lower_shadows=[]
    for j in range(i-4,i+1):
        rng=high[j]-low[j]
        if rng>0: lower_shadows.append((min(openp[j],close[j])-low[j])/rng)
    f['lower_shadow_pre5']=np.mean(lower_shadows) if lower_shadows else np.nan
    return f

def find_starts(g):
    close=g['close'].values;high=g['high'].values;dates=g['trade_date'].values;n=len(g);res=[];last=-1
    for T in range(LOOKBACK+1,n-FWD_MIN):
        if T<=last: continue
        end=min(T+FWD_MAX,n-1);wh=high[T+FWD_MIN:end+1] if T+FWD_MIN<=end else np.array([])
        if len(wh)==0: continue
        if np.max(wh)/close[T]>=DOUBLE_THR:
            lo=max(LOOKBACK+1,T-SEARCH_WIN);hi=min(n-FWD_MIN,T+SEARCH_WIN)
            tT=lo+int(np.argmin(close[lo:hi]));e2=min(tT+FWD_MAX,n-1)
            wh2=high[tT+FWD_MIN:e2+1] if tT+FWD_MIN<=e2 else np.array([])
            if len(wh2)>0 and np.max(wh2)/close[tT]>=DOUBLE_THR:
                gain=np.max(high[tT+FWD_MIN:e2+1])/close[tT]
                res.append((tT,gain));last=tT+FWD_MIN+int(np.argmax(high[tT+FWD_MIN:e2+1]))
    return res

# 采样三组
print("采样翻倍组+对照组...")
dbl_rows=[]  # 翻倍组(含gain)
ctrl_rows=[] # 超跌未翻倍对照
for code,g in daily_g.items():
    if len(g)<LOOKBACK+FWD_MAX+12: continue
    # 翻倍
    for T,gain in find_starts(g):
        ff=kline_features(g,T)
        if ff: ff['gain']=gain; ff['grp']='double'; dbl_rows.append(ff)
    # 对照:前30天跌超15%但后60天没翻倍
    lo=LOOKBACK+1;hi=len(g)-FWD_MAX-12
    if lo<hi:
        for _ in range(3):
            T=np.random.randint(lo,hi);c0=g['close'].values[T];c30=g['close'].values[max(0,T-30)]
            if c30<=0 or c0/c30-1>-0.15: continue
            e=min(T+FWD_MAX,len(g)-1);wh=g['high'].values[T+FWD_MIN:e+1] if T+FWD_MIN<=e else np.array([])
            if len(wh)>0 and np.max(wh)/c0>=DOUBLE_THR: continue
            ff=kline_features(g,T)
            if ff: ff['gain']=0;ff['grp']='control';ctrl_rows.append(ff)

dbl=pd.DataFrame(dbl_rows);ctrl=pd.DataFrame(ctrl_rows)
# 对照组下采样到与翻倍组相当
if len(ctrl)>len(dbl)*1.5: ctrl=ctrl.sample(int(len(dbl)*1.5),random_state=42)
print(f"翻倍组:{len(dbl)} 对照组(超跌未翻倍):{len(ctrl)}")
allq=pd.concat([dbl,ctrl],ignore_index=True)
allq.to_csv(f"{OUT}/kline_quant_samples.csv",index=False)

# 区分度对比
feat_cols=[c for c in dbl.columns if c not in ['gain','grp']]
rows=[]
for col in feat_cols:
    d=dbl[col].dropna();c=ctrl[col].dropna()
    if len(d)<30 or len(c)<30: continue
    ks=ks_2samp(d,c).statistic
    rows.append({'feature':col,'翻倍组中位':d.median(),'对照组中位':c.median(),
                 '翻倍组均值':d.mean(),'对照组均值':c.mean(),'KS':ks})
rank=pd.DataFrame(rows).sort_values('KS',ascending=False)
rank.to_csv(f"{OUT}/kline_quant_ranking.csv",index=False)

print(f"\n{'='*72}")
print(f"K线形态区分度: 翻倍组 vs 超跌未翻倍对照组 (KS越大越能区分)")
print(f"{'='*72}")
print(f"{'特征':<22}{'翻倍组中位':>12}{'对照组中位':>12}{'KS':>8}")
print("-"*72)
for _,r in rank.iterrows():
    print(f"{r['feature']:<22}{r['翻倍组中位']:>12.3f}{r['对照组中位']:>12.3f}{r['KS']:>8.3f}")

print(f"\n耗时{(time.time()-t0)/60:.1f}分钟")
print("产出: kline_quant_ranking.csv, kline_quant_samples.csv")
