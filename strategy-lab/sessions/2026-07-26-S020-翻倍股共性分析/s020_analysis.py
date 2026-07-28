"""
S020 翻倍股共性分析
对比正样本(翻倍起点前1天) vs 负样本(随机点)的特征分布,找区分度最强的共性
特征维度:
  1. 技术面(取自stk_factor现成指标 + 自算动量/波动)
  2. 估值(daily_basic)
  3. 个股资金(moneyflow)
  4. 龙虎榜(top_list)
  5. 板块轮动(按industry聚合:强度/资金/位置)
输出:特征分布对比表 + 区分度排序(KS统计量)
"""
import sqlite3, pandas as pd, numpy as np, time, json, os
import warnings; warnings.filterwarnings('ignore')

DB="/Users/ziruzhu/stock-data/stock_all.db"
OUT="/Users/ziruzhu/stock-tools/strategy-lab/sessions/S020"
LOOKBACK=30; FWD_MIN,FWD_MAX=10,60; DOUBLE_THR=2.0; SEARCH_WIN=5
START="20160101"; END="20260630"

t0=time.time()
print("="*60); print("S020 翻倍股共性分析"); print("="*60)

conn=sqlite3.connect(DB)
# 股票池(剔除北交所+ST+亏损)
codes=pd.read_sql("SELECT DISTINCT ts_code FROM daily",conn)['ts_code'].tolist()
codes=[c for c in codes if not c.endswith('.BJ')]
bl=set(pd.read_sql("SELECT ts_code FROM blacklist_st",conn)['ts_code'])|set(pd.read_sql("SELECT ts_code FROM blacklist_loss",conn)['ts_code'])
codes=[c for c in codes if c not in bl]
slist=pd.read_sql("SELECT ts_code,name,industry FROM stock_list",conn)
code2ind=dict(zip(slist['ts_code'],slist['industry']))
print(f"股票池:{len(codes)}只")

print("加载daily...")
daily=pd.read_sql(f"SELECT ts_code,trade_date,open,high,low,close,vol,amount,pct_chg FROM daily WHERE trade_date>='{START}' AND trade_date<='{END}'",conn)
for c in ['open','high','low','close','vol','amount','pct_chg']: daily[c]=pd.to_numeric(daily[c],errors='coerce')
daily=daily[daily['ts_code'].isin(set(codes))].dropna(subset=['close','high','low','open']).query('close>0')
daily['industry']=daily['ts_code'].map(code2ind)

print("预计算行业聚合(每天每行业:平均涨幅/资金/位置)...")
# 行业每日平均涨幅
ind_daily=daily.groupby(['trade_date','industry']).agg(
    ind_ret=('pct_chg','mean'), ind_amount=('amount','sum'), ncnt=('ts_code','count')).reset_index()
# 行业指数(累计):用每日平均涨幅累乘近似
ind_daily=ind_daily.sort_values(['industry','trade_date'])
ind_daily['ind_idx']=ind_daily.groupby('industry')['ind_ret'].transform(lambda x:(1+x/100).cumprod())
# 行业强度排名(每天各行业按20日涨幅排名)
ind_daily['ind_ret20']=ind_daily.groupby('industry')['ind_idx'].transform(lambda x:x/x.shift(20)-1)
ind_daily['ind_rank']=ind_daily.groupby('trade_date')['ind_ret20'].rank(pct=True)
# 行业位置(指数在自身120日相对位置)
ind_daily['ind_pos120']=ind_daily.groupby('industry')['ind_idx'].transform(
    lambda x:(x-x.rolling(120,min_periods=20).min())/(x.rolling(120,min_periods=20).max()-x.rolling(120,min_periods=20).min()))
# 行业资金占全市场比重
tot_amt=daily.groupby('trade_date')['amount'].sum().rename('mkt_amount')
ind_daily=ind_daily.merge(tot_amt,on='trade_date')
ind_daily['ind_amt_share']=ind_daily['ind_amount']/ind_daily['mkt_amount']
ind_idx=ind_daily.set_index(['trade_date','industry'])
print(f"  行业聚合完成,{len(ind_daily)}行")

# 预计算全市场RPS(相对强度排名):每票N日涨幅在全市场的百分位×100
print("预计算RPS(全市场涨幅排名)...")
daily=daily.sort_values(['ts_code','trade_date'])
for n in [50,120,250]:
    daily[f'ret{n}']=daily.groupby('ts_code')['close'].transform(lambda x:x/x.shift(n)-1)
    daily[f'rps{n}']=daily.groupby('trade_date')[f'ret{n}'].rank(pct=True)*100
print("  RPS完成")

# 预计算OBV(能量潮)+OBV斜率+价量背离 - 用transform避免apply丢列
print("预计算OBV...")
daily=daily.sort_values(['ts_code','trade_date'])
gb=daily.groupby('ts_code')
sign=np.sign(gb['close'].diff().fillna(0))
daily['obv']=(sign*daily['vol']).groupby(daily['ts_code']).cumsum()
gb2=daily.groupby('ts_code')
daily['obv_d20']=gb2['obv'].diff(20)
daily['vol_ma20']=gb2['vol'].transform(lambda x:x.rolling(20).mean())
daily['obv_slope20']=daily['obv_d20']/(daily['vol_ma20']*20+1e-9)
daily['px_ret20']=gb2['close'].transform(lambda x:x/x.shift(20)-1)
daily['obv_absmean20']=gb2['obv'].transform(lambda x:x.abs().rolling(20).mean())
daily['obv_ret20']=daily['obv_d20']/(daily['obv_absmean20']+1e-9)
daily['obv_divergence']=daily['obv_ret20']-np.sign(daily['px_ret20'])*daily['px_ret20'].abs()
print("  OBV完成")

# 建RPS+OBV索引(供特征提取快速查找)
rpsobv_idx=daily[['ts_code','trade_date','rps50','rps120','rps250','obv_slope20','obv_divergence']].set_index(['ts_code','trade_date'])

daily_g={c:g.reset_index(drop=True) for c,g in daily.groupby('ts_code')}
print(f"数据加载完成,耗时{time.time()-t0:.0f}秒")

# 加载估值/资金/技术指标(全量到内存较大,分表按需)
print("加载daily_basic/moneyflow/stk_factor...")
basic=pd.read_sql(f"SELECT ts_code,trade_date,turnover_rate,volume_ratio,pe_ttm,pb,ps_ttm,total_mv,circ_mv FROM daily_basic WHERE trade_date>='{START}' AND trade_date<='{END}'",conn)
for c in basic.columns[2:]: basic[c]=pd.to_numeric(basic[c],errors='coerce')
basic=basic.sort_values(['ts_code','trade_date'])
basic[['total_mv','circ_mv']]=basic.groupby('ts_code')[['total_mv','circ_mv']].ffill().bfill()
basic_idx=basic.set_index(['ts_code','trade_date'])
mf=pd.read_sql(f"SELECT ts_code,trade_date,buy_elg_amount,sell_elg_amount,buy_lg_amount,sell_lg_amount,net_mf_amount FROM moneyflow WHERE trade_date>='{START}' AND trade_date<='{END}'",conn)
for c in mf.columns[2:]: mf[c]=pd.to_numeric(mf[c],errors='coerce')
mf['elg_net']=mf['buy_elg_amount']-mf['sell_elg_amount']; mf['lg_net']=mf['buy_lg_amount']-mf['sell_lg_amount']
mf_g={c:g.reset_index(drop=True) for c,g in mf.groupby('ts_code')}
sf=pd.read_sql(f"SELECT ts_code,trade_date,macd,kdj_k,kdj_d,rsi_6,rsi_12,rsi_24,boll_upper,boll_mid,boll_lower,cci FROM stk_factor WHERE trade_date>='{START}' AND trade_date<='{END}'",conn)
for c in sf.columns[2:]: sf[c]=pd.to_numeric(sf[c],errors='coerce')
sf_idx=sf.set_index(['ts_code','trade_date'])
conn.close()
print(f"全部加载完成,耗时{time.time()-t0:.0f}秒")

def find_starts(g):
    close=g['close'].values; high=g['high'].values; dates=g['trade_date'].values; n=len(g); starts=[]; last=-1
    for T in range(LOOKBACK+1,n-FWD_MIN):
        if T<=last: continue
        if str(dates[T])>END: break
        end=min(T+FWD_MAX,n-1); wh=high[T+FWD_MIN:end+1] if T+FWD_MIN<=end else np.array([])
        if len(wh)==0: continue
        if np.max(wh)/close[T]>=DOUBLE_THR:
            lo=max(LOOKBACK+1,T-SEARCH_WIN); hi=min(n-FWD_MIN,T+SEARCH_WIN)
            tT=lo+int(np.argmin(close[lo:hi])); e2=min(tT+FWD_MAX,n-1)
            wh2=high[tT+FWD_MIN:e2+1] if tT+FWD_MIN<=e2 else np.array([])
            if len(wh2)>0 and np.max(wh2)/close[tT]>=DOUBLE_THR:
                starts.append(tT); last=tT+FWD_MIN+int(np.argmax(high[tT+FWD_MIN:e2+1]))
    return starts

def calc_features(code, g, T):
    """T=起点位置,特征用T-1及之前(起点前1天)"""
    i=T-1
    if i<LOOKBACK: return None
    close=g['close'].values; high=g['high'].values; low=g['low'].values; vol=g['vol'].values; amount=g['amount'].values
    c0=close[i]; td=g['trade_date'].values[i]; ind=code2ind.get(code)
    f={}
    # 技术-动量
    for nn in [3,5,10,20,30]: f[f'ret_{nn}']=c0/close[i-nn]-1 if i-nn>=0 and close[i-nn]>0 else np.nan
    # 波动/位置
    r=close[i-30:i+1]; dr=np.diff(r)/r[:-1]; f['vol_30d']=float(np.std(dr)) if len(dr)>0 else np.nan
    h30=high[i-30:i+1].max(); l30=low[i-30:i+1].min(); f['price_pos_30']=(c0-l30)/(h30-l30) if h30>l30 else np.nan
    win=close[i-30:i+1]; pk=np.maximum.accumulate(win); f['maxdd_30']=float(((win-pk)/pk).min())
    ma5=close[i-5:i+1].mean(); ma10=close[i-10:i+1].mean(); ma20=close[i-20:i+1].mean()
    f['close_ma5']=c0/ma5-1 if ma5>0 else np.nan; f['close_ma10']=c0/ma10-1 if ma10>0 else np.nan
    f['ma5_ma20']=ma5/ma20-1 if ma20>0 else np.nan
    f['ma_converge']=float(np.std([ma5,ma10,ma20])/c0) if c0>0 else np.nan
    v5=vol[i-5:i+1].mean(); v30=vol[i-30:i+1].mean(); f['vol_ratio']=v5/v30 if v30>0 else np.nan
    # 技术指标(stk_factor现成)
    try:
        s=sf_idx.loc[(code,td)]
        for k in ['macd','kdj_k','rsi_6','rsi_12','cci']: f[k]=float(s[k])
        f['boll_pos']=(c0-float(s['boll_lower']))/(float(s['boll_upper'])-float(s['boll_lower'])) if s['boll_upper']!=s['boll_lower'] else np.nan
    except: 
        for k in ['macd','kdj_k','rsi_6','rsi_12','cci','boll_pos']: f[k]=np.nan
    # 估值
    try:
        b=basic_idx.loc[(code,td)]
        for k in ['turnover_rate','volume_ratio','pe_ttm','pb','ps_ttm','total_mv','circ_mv']: f[k]=float(b[k])
    except:
        for k in ['turnover_rate','volume_ratio','pe_ttm','pb','ps_ttm','total_mv','circ_mv']: f[k]=np.nan
    # 个股资金(起点前5/10/20日)
    mfg=mf_g.get(code)
    if mfg is not None:
        pos=mfg.index[mfg['trade_date']<=td]
        if len(pos)>0:
            mi=pos[-1]
            for w in [5,10,20]:
                s2=max(0,mi-w+1)
                f[f'elg_net_{w}']=float(mfg['elg_net'].iloc[s2:mi+1].sum())
                f[f'net_mf_{w}']=float(mfg['net_mf_amount'].iloc[s2:mi+1].sum())
            amt5=float(amount[i-4:i+1].sum()) if i>=4 else np.nan
            f['net_mf_ratio5']=f['net_mf_5']/amt5 if amt5 and amt5>0 else np.nan
            f['elg_ratio5']=f['elg_net_5']/amt5 if amt5 and amt5>0 else np.nan
        else:
            for w in [5,10,20]: f[f'elg_net_{w}']=np.nan; f[f'net_mf_{w}']=np.nan
            f['net_mf_ratio5']=np.nan; f['elg_ratio5']=np.nan
    # 板块轮动(起点前1天所在行业)
    try:
        ii=ind_idx.loc[(td,ind)]
        f['ind_ret20']=float(ii['ind_ret20']); f['ind_rank']=float(ii['ind_rank'])
        f['ind_pos120']=float(ii['ind_pos120']); f['ind_amt_share']=float(ii['ind_amt_share'])
    except:
        for k in ['ind_ret20','ind_rank','ind_pos120','ind_amt_share']: f[k]=np.nan
    # RPS+OBV(起点前1天) - 从rpsobv_idx索引查找(快)
    try:
        row=rpsobv_idx.loc[(code,td)]
        for n in [50,120,250]: f[f'rps{n}']=float(row[f'rps{n}'])
        f['obv_slope20']=float(row['obv_slope20']); f['obv_divergence']=float(row['obv_divergence'])
    except:
        for n in [50,120,250]: f[f'rps{n}']=np.nan
        f['obv_slope20']=np.nan; f['obv_divergence']=np.nan
    return f

# 采样正负样本
print("\n定位翻倍起点(正样本)+随机负样本...")
np.random.seed(42)
pos_rows=[]; neg_rows=[]
for code,g in daily_g.items():
    if len(g)<LOOKBACK+FWD_MAX+2: continue
    for T in find_starts(g):
        ff=calc_features(code,g,T)
        if ff: ff['label']=1; pos_rows.append(ff)
    # 负样本:每票随机1个点(其后不翻倍)
    lo=LOOKBACK+1; hi=len(g)-FWD_MAX-1
    if lo<hi:
        for _ in range(2):
            T=np.random.randint(lo,hi); c0=g['close'].values[T]; e=min(T+FWD_MAX,len(g)-1)
            wh=g['high'].values[T+FWD_MIN:e+1] if T+FWD_MIN<=e else np.array([])
            if len(wh)>0 and np.max(wh)/c0>=DOUBLE_THR: continue
            ff=calc_features(code,g,T)
            if ff: ff['label']=0; neg_rows.append(ff)

pos_df=pd.DataFrame(pos_rows); neg_df=pd.DataFrame(neg_rows)
print(f"正样本:{len(pos_df)} 负样本:{len(neg_df)}")
alldf=pd.concat([pos_df,neg_df],ignore_index=True)
alldf.to_csv(f"{OUT}/s020_samples.csv",index=False)

# 分布对比 + 区分度(KS统计量)
from scipy.stats import ks_2samp
feat_cols=[c for c in alldf.columns if c!='label']
rows=[]
for col in feat_cols:
    p=pos_df[col].dropna(); n=neg_df[col].dropna()
    if len(p)<30 or len(n)<30: continue
    ks=ks_2samp(p,n).statistic
    rows.append({'feature':col,'pos_median':p.median(),'neg_median':n.median(),
                 'pos_mean':p.mean(),'neg_mean':n.mean(),'KS':ks,
                 'diff_ratio':(p.median()-n.median())/(abs(n.median())+1e-9)})
rank=pd.DataFrame(rows).sort_values('KS',ascending=False)
rank.to_csv(f"{OUT}/s020_feature_ranking.csv",index=False)

print(f"\n{'='*60}\n特征区分度排名(KS统计量,越大区分力越强)\n{'='*60}")
print(f"{'特征':<18}{'翻倍股中位':>12}{'普通股中位':>12}{'KS':>8}")
print("-"*55)
for _,r in rank.head(25).iterrows():
    print(f"{r['feature']:<18}{r['pos_median']:>12.3f}{r['neg_median']:>12.3f}{r['KS']:>8.3f}")

print(f"\n全部完成,耗时{(time.time()-t0)/60:.1f}分钟")
print(f"产出: s020_samples.csv, s020_feature_ranking.csv")
