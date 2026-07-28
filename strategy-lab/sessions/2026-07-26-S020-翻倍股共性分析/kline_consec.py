"""
连续涨停妖股识别 + 剔除后重新量化对比
逻辑:
  1. 对每个翻倍起点,检查T+1~T+10天内有没有"连续2日以上涨停(>=9.5%)"
  2. 把翻倍组拆成:A1=有连续涨停(妖股) / A2=无连续涨停(温和趋势)
  3. A1/A2/C三组分别统计基本特征分布
  4. 重点对A2 vs C做KS区分度(剔除妖股后的干净对比)
结论:如果A2 vs C区分度提升,说明妖股确实在污染分析
"""
import sqlite3,pandas as pd,numpy as np,time
from scipy.stats import ks_2samp
import warnings; warnings.filterwarnings('ignore')

DB="/Users/ziruzhu/stock-data/stock_all.db"
OUT="/Users/ziruzhu/stock-tools/strategy-lab/sessions/S020"
LOOKBACK=30; FWD_MIN,FWD_MAX=10,60; DOUBLE_THR=2.0; SEARCH_WIN=5
LIMIT_UP=0.095   # 涨停阈值(A股非ST=9.5%以上视为涨停)
CONSEC_MIN=2     # 连续涨停天数阈值
np.random.seed(42)

t0=time.time()
print("加载数据...")
conn=sqlite3.connect(DB)
codes=pd.read_sql("SELECT DISTINCT ts_code FROM daily",conn)['ts_code'].tolist()
codes=[c for c in codes if not c.endswith('.BJ')]
bl=set(pd.read_sql("SELECT ts_code FROM blacklist_st",conn)['ts_code'])|set(pd.read_sql("SELECT ts_code FROM blacklist_loss",conn)['ts_code'])
codes=[c for c in codes if c not in bl]
daily=pd.read_sql("SELECT ts_code,trade_date,open,high,low,close,vol FROM daily WHERE trade_date>='20160101' AND trade_date<='20260630' ORDER BY ts_code,trade_date",conn)
for c in ['open','high','low','close','vol']: daily[c]=pd.to_numeric(daily[c],errors='coerce')
daily=daily[daily['ts_code'].isin(set(codes))].dropna(subset=['open','high','low','close']).query('close>0')
conn.close()
daily_g={c:g.reset_index(drop=True) for c,g in daily.groupby('ts_code')}
print(f"股票:{len(daily_g)},耗时{time.time()-t0:.0f}秒")

def has_consec_limitup(g, T, window=10, consec=CONSEC_MIN):
    """T起点后window天内有没有>=consec天连续涨停"""
    end=min(T+window, len(g)-1)
    if end<=T: return False
    close=g['close'].values; prev=g['close'].values
    rets=[]
    for j in range(T, end):
        if prev[j]>0: rets.append((close[j]-close[j-1])/close[j-1])
        else: rets.append(0)
    # 连续涨停计数
    max_consec=0; cur=0
    for r in rets:
        if r>=LIMIT_UP: cur+=1; max_consec=max(max_consec,cur)
        else: cur=0
    return max_consec>=consec

def kline_features(g, T):
    i=T-1
    if i<LOOKBACK or T+10>=len(g): return None
    close=g['close'].values;high=g['high'].values;low=g['low'].values;openp=g['open'].values;vol=g['vol'].values
    c0=close[i];f={}
    v_pre5=vol[i-4:i+1].mean();v_pre20=vol[i-19:i+1].mean();v_pre60=vol[max(0,i-59):i+1].mean()
    f['vol_shrink_5_20']=v_pre5/v_pre20 if v_pre20>0 else np.nan
    f['vol_shrink_5_60']=v_pre5/v_pre60 if v_pre60>0 else np.nan
    v_post5=vol[T:T+5].mean()
    f['vol_expand_post5']=v_post5/v_pre20 if v_pre20>0 else np.nan
    f['vol_max_post10']=vol[T:T+10].max()/v_pre20 if v_pre20>0 else np.nan
    f['ret_pre20']=c0/close[i-19]-1 if close[i-19]>0 else np.nan
    f['ret_pre60']=c0/close[max(0,i-59)]-1 if close[max(0,i-59)]>0 else np.nan
    daily_ret=np.diff(close[i-20:i+1])/close[i-20:i]
    max_drop=abs(daily_ret.min()) if len(daily_ret)>0 else np.nan
    cum_drop=abs(min(0,c0/close[i-19]-1)) if close[i-19]>0 else np.nan
    f['drop_concentration']=max_drop/cum_drop if cum_drop and cum_drop>0 else np.nan
    f['down_days_ratio']=(daily_ret<0).mean() if len(daily_ret)>0 else np.nan
    ma5=close[i-4:i+1].mean();ma10=close[i-9:i+1].mean();ma20=close[i-19:i+1].mean()
    f['ma_converge']=np.std([ma5,ma10,ma20])/c0 if c0>0 else np.nan
    f['close_below_ma20']=c0/ma20-1 if ma20>0 else np.nan
    post_ret=(close[T:T+10]-close[T-1:T+9])/close[T-1:T+9]
    big_up=np.where(post_ret>0.07)[0]
    f['first_bigup_day']=int(big_up[0])+1 if len(big_up)>0 else 99
    f['ret_post3']=close[min(T+2,len(g)-1)]/c0-1 if c0>0 else np.nan
    lower_shadows=[]
    for j in range(i-4,i+1):
        rng=high[j]-low[j]
        if rng>0: lower_shadows.append((min(openp[j],close[j])-low[j])/rng)
    f['lower_shadow_pre5']=np.mean(lower_shadows) if lower_shadows else np.nan
    return f

def find_starts_with_gain(g):
    close=g['close'].values;high=g['high'].values;n=len(g);res=[];last=-1
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

print("采样三组...")
a1_rows=[]; a2_rows=[]; ctrl_rows=[]
for code,g in daily_g.items():
    if len(g)<LOOKBACK+FWD_MAX+12: continue
    for T,gain in find_starts_with_gain(g):
        ff=kline_features(g,T)
        if not ff: continue
        ff['gain']=gain
        if has_consec_limitup(g,T):
            ff['grp']='A1_妖股'; a1_rows.append(ff)
        else:
            ff['grp']='A2_趋势'; a2_rows.append(ff)
    lo=LOOKBACK+1;hi=len(g)-FWD_MAX-12
    if lo<hi:
        for _ in range(3):
            T=np.random.randint(lo,hi);c0=g['close'].values[T];c30=g['close'].values[max(0,T-30)]
            if c30<=0 or c0/c30-1>-0.15: continue
            e=min(T+FWD_MAX,len(g)-1);wh=g['high'].values[T+FWD_MIN:e+1] if T+FWD_MIN<=e else np.array([])
            if len(wh)>0 and np.max(wh)/c0>=DOUBLE_THR: continue
            ff=kline_features(g,T)
            if ff: ff['gain']=0;ff['grp']='C_对照';ctrl_rows.append(ff)

a1=pd.DataFrame(a1_rows); a2=pd.DataFrame(a2_rows); ctrl=pd.DataFrame(ctrl_rows)
if len(ctrl)>len(a2)*1.5: ctrl=ctrl.sample(int(len(a2)*1.5),random_state=42)
print(f"\nA1妖股(连续涨停翻倍):{len(a1)} ({len(a1)/(len(a1)+len(a2))*100:.1f}%)")
print(f"A2趋势(无连续涨停翻倍):{len(a2)} ({len(a2)/(len(a1)+len(a2))*100:.1f}%)")
print(f"C对照(超跌未翻倍):{len(ctrl)}")
print(f"\n妖股比例={len(a1)/(len(a1)+len(a2))*100:.1f}% — 这就是'污染'的规模")

feat_cols=[c for c in a2.columns if c not in ['gain','grp']]

# A1妖股 vs A2趋势 的特征差异(看妖股有没有自己独特的起点特征)
print(f"\n{'='*72}")
print("【一】妖股(A1) vs 趋势翻倍(A2): 起点前特征有无显著差异")
print(f"{'='*72}")
print(f"{'特征':<22}{'A1妖股中位':>12}{'A2趋势中位':>12}{'KS':>8}")
print("-"*72)
rows_a1a2=[]
for col in feat_cols:
    d1=a1[col].dropna(); d2=a2[col].dropna()
    if len(d1)<20 or len(d2)<20: continue
    ks=ks_2samp(d1,d2).statistic
    rows_a1a2.append({'feature':col,'A1中位':d1.median(),'A2中位':d2.median(),'KS':ks})
ra1a2=pd.DataFrame(rows_a1a2).sort_values('KS',ascending=False)
for _,r in ra1a2.iterrows():
    print(f"{r['feature']:<22}{r['A1中位']:>12.3f}{r['A2中位']:>12.3f}{r['KS']:>8.3f}")

# A2趋势翻倍 vs C对照 (剔除妖股后的干净对比)
print(f"\n{'='*72}")
print("【二】剔除妖股后: A2趋势翻倍 vs C超跌未翻倍 (干净信号)")
print(f"{'='*72}")
print(f"{'特征':<22}{'A2趋势中位':>12}{'C对照中位':>12}{'KS':>8}")
print("-"*72)
rows_a2c=[]
for col in feat_cols:
    d=a2[col].dropna(); c=ctrl[col].dropna()
    if len(d)<20 or len(c)<20: continue
    ks=ks_2samp(d,c).statistic
    rows_a2c.append({'feature':col,'A2中位':d.median(),'C中位':c.median(),'KS':ks})
ra2c=pd.DataFrame(rows_a2c).sort_values('KS',ascending=False)
for _,r in ra2c.iterrows():
    print(f"{r['feature']:<22}{r['A2中位']:>12.3f}{r['C中位']:>12.3f}{r['KS']:>8.3f}")
print(f"\nTop3最强特征(剔妖股后): {', '.join(ra2c.head(3)['feature'].tolist())}")

ra1a2.to_csv(f"{OUT}/kline_a1vsa2_ranking.csv",index=False)
ra2c.to_csv(f"{OUT}/kline_a2vsc_ranking.csv",index=False)
pd.concat([a1,a2,ctrl]).to_csv(f"{OUT}/kline_threeway_samples.csv",index=False)
print(f"\n耗时{(time.time()-t0)/60:.1f}分钟")
