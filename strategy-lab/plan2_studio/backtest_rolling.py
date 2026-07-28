"""
滚动训练回测引擎 - 全周期2016-2026,逐年滚动,全程样本外
流程:
  for 回测年 in [2019,2020,...,2026上半年]:
    1. 用 2016~(回测年-1) 的数据训练模型
    2. 在回测年每周扫描,模型打分≥阈值→买入信号
    3. 按4套卖出规则模拟交易,记录单笔结果
  最后汇总所有年份的单笔统计 + 拼接组合净值曲线
严格防前视:每年模型只用该年之前的数据训练
"""
import sqlite3, pandas as pd, numpy as np, lightgbm as lgb, os, time
from datetime import datetime
from multiprocessing import Pool
import warnings; warnings.filterwarnings('ignore')

DB_PATH  = "/Users/ziruzhu/stock-data/stock_all.db"
OUTDIR   = "/Users/ziruzhu/stock-tools/strategy-lab/plan2_studio"
LOOKBACK = 30
FWD_MIN, FWD_MAX = 10, 60
DOUBLE_THR = 2.0
SEARCH_WIN = 5
BUY_THR  = 0.5
COST     = 0.003
INIT_CAP = 1_000_000
MAX_HOLD_P = 10
N_WORKERS = 12

# 滚动: (训练起, 训练止, 回测年起, 回测年止)
ROLL_WINDOWS = [
    ("20160101","20181231","20190101","20191231"),
    ("20160101","20191231","20200101","20201231"),
    ("20160101","20201231","20210101","20211231"),
    ("20160101","20211231","20220101","20221231"),
    ("20160101","20221231","20230101","20231231"),
    ("20160101","20231231","20240101","20241231"),
    ("20160101","20241231","20250101","20251231"),
    ("20160101","20251231","20260101","20260630"),
]

SELL_RULES = {
    'A_翻倍止盈+止损20+到期60': {'tp':1.0, 'sl':-0.20,'maxhold':60},
    'B_固定持有60天':           {'tp':None,'sl':None,  'maxhold':60},
    'C_止盈30+止损15+到期60':   {'tp':0.30,'sl':-0.15, 'maxhold':60},
    'D_止盈50+止损20+到期90':   {'tp':0.50,'sl':-0.20, 'maxhold':90},
}

from features import make_features, FEATURE_NAMES

# ===== worker: 单票构造训练样本(某个训练截止日之前) =====
_G=None; _MF=None; _TL=None; _BI=None
def _init(daily_g, mf_g, tl_g, basic_idx):
    global _G,_MF,_TL,_BI
    _G=daily_g; _MF=mf_g; _TL=tl_g; _BI=basic_idx

def _find_starts(close,high,dates,n,end_date):
    starts=[]; last_end=-1
    for T in range(LOOKBACK+1,n-FWD_MIN):
        if T<=last_end: continue
        if str(dates[T])>end_date: break
        end=min(T+FWD_MAX,n-1)
        wh=high[T+FWD_MIN:end+1] if T+FWD_MIN<=end else np.array([])
        if len(wh)==0: continue
        if np.max(wh)/close[T]>=DOUBLE_THR:
            lo=max(LOOKBACK+1,T-SEARCH_WIN); hi=min(n-FWD_MIN,T+SEARCH_WIN)
            tT=lo+int(np.argmin(close[lo:hi])); e2=min(tT+FWD_MAX,n-1)
            wh2=high[tT+FWD_MIN:e2+1] if tT+FWD_MIN<=e2 else np.array([])
            if len(wh2)>0 and np.max(wh2)/close[tT]>=DOUBLE_THR:
                starts.append(tT); last_end=tT+FWD_MIN+int(np.argmax(high[tT+FWD_MIN:e2+1]))
    return starts

def build_train_samples(args):
    """构造单票在train_end之前的训练样本"""
    code, train_start, train_end = args
    g=_G.get(code)
    if g is None or len(g)<LOOKBACK+FWD_MAX+2: return ([],[])
    gg=g[(g['trade_date']>=train_start)&(g['trade_date']<=train_end)].reset_index(drop=True)
    if len(gg)<LOOKBACK+FWD_MAX+2: return ([],[])
    close=gg['close'].values; high=gg['high'].values; dates=gg['trade_date'].values; n=len(gg)
    mfg=_MF.get(code); tlg=_TL.get(code)
    starts=_find_starts(close,high,dates,n,train_end)
    pos=[]
    for T in starts:
        f=make_features(gg,T,LOOKBACK,mfg,tlg,_BI,code,{})
        if f is None: continue
        vals=[f.get(k,np.nan) for k in FEATURE_NAMES]
        if any(v is None or (isinstance(v,float) and np.isnan(v)) for v in vals): continue
        pos.append(vals+[1])
    rng=np.random.RandomState(hash(code)&0xffffffff)
    neg=[]; lo=LOOKBACK+1; hi=n-FWD_MAX-1
    if lo<hi:
        for _ in range(3):
            T=rng.randint(lo,hi); c0=close[T]; e=min(T+FWD_MAX,n-1)
            wh=high[T+FWD_MIN:e+1] if T+FWD_MIN<=e else np.array([])
            if len(wh)>0 and np.max(wh)/c0>=DOUBLE_THR: continue
            f=make_features(gg,T,LOOKBACK,mfg,tlg,_BI,code,{})
            if f is None: continue
            vals=[f.get(k,np.nan) for k in FEATURE_NAMES]
            if any(v is None or (isinstance(v,float) and np.isnan(v)) for v in vals): continue
            neg.append(vals+[0])
    return (pos,neg)

def train_model(daily_g, mf_g, tl_g, basic_idx, codes, train_start, train_end):
    """训练一个模型(用train_end之前的数据)"""
    args=[(c,train_start,train_end) for c in codes]
    with Pool(N_WORKERS, initializer=_init, initargs=(daily_g,mf_g,tl_g,basic_idx)) as pool:
        res=pool.map(build_train_samples, args, chunksize=20)
    pos=[]; neg=[]
    for p,n in res: pos.extend(p); neg.extend(n)
    np.random.seed(42)
    if len(neg)>len(pos)*3:
        idx=np.random.choice(len(neg),len(pos)*3,replace=False); neg=[neg[i] for i in idx]
    data=pd.DataFrame(pos+neg, columns=FEATURE_NAMES+['label'])
    if len(data)<100 or data['label'].nunique()<2: return None, 0, 0
    X=data[FEATURE_NAMES]; y=data['label']
    params=dict(objective='binary',metric='auc',verbose=-1,is_unbalance=True,num_threads=N_WORKERS,
                num_leaves=31,max_depth=5,learning_rate=0.03,feature_fraction=0.8,
                bagging_fraction=0.8,bagging_freq=5,min_child_samples=30)
    m=lgb.train(params, lgb.Dataset(X,y), num_boost_round=300)
    return m, len(pos), len(neg)

def simulate(g, entry_i, rule):
    if entry_i>=len(g): return None
    bp=g['open'].values[entry_i]
    if bp<=0: return None
    cls=g['close'].values; hi=g['high'].values; lo=g['low'].values
    tp=rule['tp']; sl=rule['sl']; mh=rule['maxhold']
    for d in range(1,mh+1):
        j=entry_i+d
        if j>=len(g): return (cls[-1]/bp-1-COST, d, 'data_end')
        if tp and hi[j]/bp-1>=tp: return (tp-COST, d, 'tp')
        if sl and lo[j]/bp-1<=sl: return (sl-COST, d, 'sl')
    j=min(entry_i+mh,len(g)-1)
    return (cls[j]/bp-1-COST, mh, 'time')

if __name__=='__main__':
    t0=time.time()
    print("="*60); print("滚动训练回测 2016-2026 全周期"); print("="*60)

    conn=sqlite3.connect(DB_PATH)
    all_codes=pd.read_sql("SELECT DISTINCT ts_code FROM daily",conn)['ts_code'].tolist()
    all_codes=[c for c in all_codes if not c.endswith('.BJ')]
    bl=set(pd.read_sql("SELECT ts_code FROM blacklist_st",conn)['ts_code'])|set(pd.read_sql("SELECT ts_code FROM blacklist_loss",conn)['ts_code'])
    all_codes=[c for c in all_codes if c not in bl]
    stock_list=pd.read_sql("SELECT ts_code,name,industry FROM stock_list",conn)
    print(f"股票池:{len(all_codes)}只(剔除北交所+ST+亏损)")

    print("批量加载全周期数据(2016-2026)...")
    BATCH=500
    dl=[]; bl_=[]; ml=[]; tll=[]
    for i in range(0,len(all_codes),BATCH):
        b=all_codes[i:i+BATCH]; ph=','.join(['?']*len(b))
        d=pd.read_sql(f"SELECT ts_code,trade_date,open,high,low,close,vol,amount FROM daily WHERE ts_code IN ({ph}) AND trade_date>='20160101' AND trade_date<='20260630' ORDER BY ts_code,trade_date",conn,params=b)
        for c in ['open','high','low','close','vol','amount']: d[c]=pd.to_numeric(d[c],errors='coerce')
        dl.append(d)
        bb=pd.read_sql(f"SELECT ts_code,trade_date,turnover_rate,pe_ttm,pb,ps_ttm,total_mv,circ_mv FROM daily_basic WHERE ts_code IN ({ph}) AND trade_date>='20160101' AND trade_date<='20260630'",conn,params=b)
        for c in ['turnover_rate','pe_ttm','pb','ps_ttm','total_mv','circ_mv']: bb[c]=pd.to_numeric(bb[c],errors='coerce')
        bl_.append(bb)
        mm=pd.read_sql(f"SELECT ts_code,trade_date,buy_elg_amount,sell_elg_amount,buy_lg_amount,sell_lg_amount,net_mf_amount FROM moneyflow WHERE ts_code IN ({ph}) AND trade_date>='20160101' AND trade_date<='20260630' ORDER BY ts_code,trade_date",conn,params=b)
        for c in ['buy_elg_amount','sell_elg_amount','buy_lg_amount','sell_lg_amount','net_mf_amount']: mm[c]=pd.to_numeric(mm[c],errors='coerce')
        mm['elg_net']=mm['buy_elg_amount']-mm['sell_elg_amount']; mm['lg_net']=mm['buy_lg_amount']-mm['sell_lg_amount']
        ml.append(mm)
        tt=pd.read_sql(f"SELECT trade_date,ts_code,net_amount FROM top_list WHERE ts_code IN ({ph}) AND trade_date>='20160101' AND trade_date<='20260630'",conn,params=b)
        tt['net_amount']=pd.to_numeric(tt['net_amount'],errors='coerce'); tll.append(tt)
    conn.close()
    daily=pd.concat(dl,ignore_index=True).dropna(subset=['close','high','low','open']).query('close>0 and open>0 and low>0')
    basic=pd.concat(bl_,ignore_index=True).sort_values(['ts_code','trade_date'])
    basic[['total_mv','circ_mv']]=basic.groupby('ts_code')[['total_mv','circ_mv']].ffill().bfill()
    basic_idx=basic.set_index(['ts_code','trade_date'])
    mf=pd.concat(ml,ignore_index=True); tl=pd.concat(tll,ignore_index=True)
    daily_g={c:g.reset_index(drop=True) for c,g in daily.groupby('ts_code')}
    mf_g={c:g.reset_index(drop=True) for c,g in mf.groupby('ts_code')}
    tl_g={c:g.reset_index(drop=True) for c,g in tl.groupby('ts_code')}
    print(f"加载完成,耗时{time.time()-t0:.0f}秒,有效股{len(daily_g)}只")

    # ===== 逐年滚动 =====
    all_trades={rn:[] for rn in SELL_RULES}   # 每套规则的单笔收益(跨年累积)
    all_signals=[]                             # 所有信号(供组合回测)
    year_summary=[]
    for (ts,te,bs,be) in ROLL_WINDOWS:
        yr=bs[:4]
        print(f"\n{'='*50}\n回测年{yr}: 训练{ts[:4]}-{te[:4]} → 回测{bs}~{be}\n{'='*50}")
        model,npos,nneg=train_model(daily_g,mf_g,tl_g,basic_idx,all_codes,ts,te)
        if model is None: print("  训练样本不足,跳过"); continue
        print(f"  训练完成(正{npos}/负{nneg}),生成{yr}年信号...")
        # 该年调仓日(每5交易日)
        ydates=sorted(daily[(daily['trade_date']>=bs)&(daily['trade_date']<=be)]['trade_date'].unique())
        rebal=ydates[::5]
        ycnt=0
        for rdate in rebal:
            fb=[]; cb=[]
            for code,g in daily_g.items():
                p=g.index[g['trade_date']==rdate]
                if len(p)==0: continue
                T=int(p[0])
                if T<LOOKBACK+1 or T+1>=len(g): continue
                f=make_features(g,T,LOOKBACK,mf_g.get(code),tl_g.get(code),basic_idx,code,{})
                if f is None: continue
                vals=[f.get(k,np.nan) for k in FEATURE_NAMES]
                if any(v is None or (isinstance(v,float) and np.isnan(v)) for v in vals): continue
                fb.append(vals); cb.append((code,T+1))
            if fb:
                scores=model.predict(pd.DataFrame(fb,columns=FEATURE_NAMES))
                for (code,ei),sc in zip(cb,scores):
                    if sc>=BUY_THR:
                        all_signals.append({'rebal_date':rdate,'code':code,'entry_i':ei,'score':sc,'year':yr})
                        ycnt+=1
        print(f"  {yr}年信号数:{ycnt}")
        year_summary.append((yr,npos,nneg,ycnt))

    sig_df=pd.DataFrame(all_signals)
    sig_df.to_csv(f"{OUTDIR}/roll_signals.csv",index=False)
    print(f"\n全周期总信号:{len(sig_df)}")

    # ===== 口径A: 单笔统计(全周期) =====
    report=[]; 
    def log(s): print(s); report.append(s)
    log(f"\n{'#'*60}\n口径A: 单笔独立统计(全周期2019-2026样本外)\n{'#'*60}")
    for rname,rule in SELL_RULES.items():
        rets=[]; days=[]
        for _,row in sig_df.iterrows():
            g=daily_g.get(row['code'])
            if g is None: continue
            res=simulate(g,int(row['entry_i']),rule)
            if res: rets.append(res[0]); days.append(res[1])
        rets=np.array(rets)
        if len(rets)==0: continue
        win=(rets>0).mean(); avg=rets.mean(); med=np.median(rets)
        aw=rets[rets>0].mean() if (rets>0).any() else 0; al=rets[rets<=0].mean() if (rets<=0).any() else 0
        pl=abs(aw/al) if al!=0 else 999
        log(f"\n【{rname}】信号{len(rets)} 胜率{win:.2%} 平均{avg:.2%} 中位{med:.2%} 盈亏比{pl:.2f} 平均持有{np.mean(days):.0f}天")
        bins=[-1,-0.2,-0.1,0,0.1,0.3,0.5,1.0,99]; labels=['<-20%','-20~-10%','-10~0%','0~10%','10~30%','30~50%','50~100%','>100%']
        dist=pd.cut(rets,bins=bins,labels=labels).value_counts().sort_index()
        log("  分布:"+" ".join([f"{l}:{c}({c/len(rets)*100:.0f}%)" for l,c in dist.items()]))

    # ===== 口径B: 组合净值(全周期) =====
    log(f"\n{'#'*60}\n口径B: 组合净值(100万,最多{MAX_HOLD_P}只,全周期滚动)\n{'#'*60}")
    all_rebal=sorted(sig_df['rebal_date'].unique())
    for rname,rule in SELL_RULES.items():
        cap=float(INIT_CAP); positions={}; nav_c=[]; nav_d=[]; peak=cap; mdd=0
        for rdate in all_rebal:
            tc=[]
            for code,pos in positions.items():
                g=daily_g.get(code)
                if g is None: tc.append(code); continue
                cur=g.index[g['trade_date']==rdate]
                if len(cur)==0: continue
                ci=int(cur[0]); held=ci-pos['ei']; bp=pos['bp']; cp=g['close'].values[ci]
                hs=g['high'].values[pos['ei']:ci+1].max() if ci>pos['ei'] else cp; ln=g['low'].values[ci]
                ex=False; er=cp/bp-1
                if rule['tp'] and hs/bp-1>=rule['tp']: ex=True; er=rule['tp']
                elif rule['sl'] and ln/bp-1<=rule['sl']: ex=True; er=rule['sl']
                elif held>=rule['maxhold']: ex=True
                if ex: cap+=pos['sh']*bp*(1+er-COST); tc.append(code)
            for c in tc: positions.pop(c,None)
            slots=MAX_HOLD_P-len(positions)
            if slots>0:
                ds=sig_df[sig_df['rebal_date']==rdate].sort_values('score',ascending=False)
                budget=cap/max(slots,1)
                for _,s in ds.iterrows():
                    if len(positions)>=MAX_HOLD_P: break
                    code=s['code']
                    if code in positions: continue
                    g=daily_g.get(code)
                    if g is None: continue
                    ei=int(s['entry_i'])
                    if ei>=len(g): continue
                    bp=float(g['open'].values[ei])
                    if bp<=0 or budget<100*bp: continue
                    sh=int(budget/bp/100)*100
                    if sh<100: continue
                    c_=sh*bp*(1+COST/2)
                    if c_>cap: continue
                    cap-=c_; positions[code]={'ei':ei,'bp':bp,'sh':sh}
            mv=cap
            for code,pos in positions.items():
                g=daily_g.get(code)
                if g is None: continue
                cur=g.index[g['trade_date']==rdate]
                if len(cur)>0: mv+=pos['sh']*float(g['close'].values[int(cur[0])])
            nav_c.append(mv); nav_d.append(rdate); peak=max(peak,mv); mdd=min(mdd,(mv-peak)/peak)
        nav=np.array(nav_c); total=nav[-1]/INIT_CAP-1
        d0=datetime.strptime(all_rebal[0],'%Y%m%d'); d1=datetime.strptime(all_rebal[-1],'%Y%m%d'); yrs=(d1-d0).days/365
        ann=(nav[-1]/INIT_CAP)**(1/yrs)-1 if yrs>0 else 0
        rs=np.diff(nav)/nav[:-1]; shp=rs.mean()/rs.std()*np.sqrt(50) if rs.std()>0 else 0
        log(f"\n【{rname}】期末{nav[-1]:,.0f} 总收益{total:.2%} 年化{ann:.2%} 最大回撤{mdd:.2%} 夏普{shp:.2f}")
        pd.DataFrame({'date':nav_d,'nav':nav_c}).to_csv(f"{OUTDIR}/roll_nav_{rname[:1]}.csv",index=False)

    log(f"\n各年信号: "+" ".join([f"{y}:{s}" for y,_,_,s in year_summary]))
    with open(f"{OUTDIR}/roll_backtest_report.txt",'w') as fp: fp.write("\n".join(report))
    print(f"\n全部完成! 耗时{(time.time()-t0)/60:.1f}分钟")
