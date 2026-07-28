"""
交易回测引擎 v2 - 性能优化版
一次性批量加载测试期数据到内存 → 向量化计算信号 → 模拟交易
测试期: 2024-01 ~ 2026-06
"""
import sqlite3, pandas as pd, numpy as np, lightgbm as lgb, os, time
from datetime import datetime
import warnings; warnings.filterwarnings('ignore')

# ---- 配置 ----
DB_PATH    = "/Users/ziruzhu/stock-data/stock_all.db"
MODEL_PATH = "/Users/ziruzhu/stock-tools/strategy-lab/plan2_studio/plan2_model.txt"
OUTDIR     = "/Users/ziruzhu/stock-tools/strategy-lab/plan2_studio"
LOOKBACK   = 30
BUY_THR    = 0.5
COST       = 0.003     # 双边总成本(佣金+印花税+滑点)
INIT_CAP   = 1_000_000
MAX_HOLD_P = 10        # 组合最多持仓数
TEST_START = "20240101"
TEST_END   = "20260630"
DATA_START = "20220101"  # 特征需要历史数据，从更早开始加载

SELL_RULES = {
    'A_翻倍止盈+止损20+到期60': {'tp':1.0, 'sl':-0.20, 'maxhold':60},
    'B_固定持有60天':           {'tp':None,'sl':None,   'maxhold':60},
    'C_止盈30+止损15+到期60':   {'tp':0.30,'sl':-0.15,  'maxhold':60},
    'D_止盈50+止损20+到期90':   {'tp':0.50,'sl':-0.20,  'maxhold':90},
}

from features import make_features, FEATURE_NAMES

def _load_basic_features(conn, codes_in, start, end):
    ph = ','.join(['?']*len(codes_in))
    b = pd.read_sql(f"SELECT ts_code,trade_date,turnover_rate,pe_ttm,pb,ps_ttm,total_mv,circ_mv FROM daily_basic WHERE ts_code IN ({ph}) AND trade_date>=? AND trade_date<=? ORDER BY ts_code,trade_date", conn, params=codes_in+[start,end])
    for c in ['turnover_rate','pe_ttm','pb','ps_ttm','total_mv','circ_mv']: b[c]=pd.to_numeric(b[c],errors='coerce')
    return b

def _load_mf(conn, codes_in, start, end):
    ph = ','.join(['?']*len(codes_in))
    m = pd.read_sql(f"SELECT ts_code,trade_date,buy_elg_amount,sell_elg_amount,buy_lg_amount,sell_lg_amount,net_mf_amount FROM moneyflow WHERE ts_code IN ({ph}) AND trade_date>=? AND trade_date<=? ORDER BY ts_code,trade_date", conn, params=codes_in+[start,end])
    for c in ['buy_elg_amount','sell_elg_amount','buy_lg_amount','sell_lg_amount','net_mf_amount']: m[c]=pd.to_numeric(m[c],errors='coerce')
    m['elg_net']=m['buy_elg_amount']-m['sell_elg_amount']; m['lg_net']=m['buy_lg_amount']-m['sell_lg_amount']
    return m

def _load_tl(conn, codes_in, start, end):
    ph = ','.join(['?']*len(codes_in))
    t = pd.read_sql(f"SELECT trade_date,ts_code,net_amount FROM top_list WHERE ts_code IN ({ph}) AND trade_date>=? AND trade_date<=?", conn, params=codes_in+[start,end])
    t['net_amount']=pd.to_numeric(t['net_amount'],errors='coerce')
    return t

if __name__=='__main__':
    t0=time.time()
    print("="*60); print("交易回测 v2"); print("="*60)

    # ---- 加载股票池 ----
    conn=sqlite3.connect(DB_PATH)
    all_codes = pd.read_sql("SELECT DISTINCT ts_code FROM daily",conn)['ts_code'].tolist()
    all_codes = [c for c in all_codes if not c.endswith('.BJ')]
    bl = set(pd.read_sql("SELECT ts_code FROM blacklist_st",conn)['ts_code']) | set(pd.read_sql("SELECT ts_code FROM blacklist_loss",conn)['ts_code'])
    all_codes = [c for c in all_codes if c not in bl]
    stock_list = pd.read_sql("SELECT ts_code,name,industry FROM stock_list",conn)
    print(f"股票池:{len(all_codes)}只")

    # ---- 分批加载数据(每批500只,避免IN子句过长) ----
    BATCH = 500
    print(f"\n批量加载数据({DATA_START}~{TEST_END})...")
    all_daily=[]; all_basic=[]; all_mf=[]; all_tl=[]
    for i in range(0, len(all_codes), BATCH):
        batch = all_codes[i:i+BATCH]
        ph = ','.join(['?']*len(batch))
        d = pd.read_sql(f"SELECT ts_code,trade_date,open,high,low,close,vol,amount FROM daily WHERE ts_code IN ({ph}) AND trade_date>=? AND trade_date<=? ORDER BY ts_code,trade_date", conn, params=batch+[DATA_START,TEST_END])
        for c in ['open','high','low','close','vol','amount']: d[c]=pd.to_numeric(d[c],errors='coerce')
        all_daily.append(d)
        all_basic.append(_load_basic_features(conn, batch, DATA_START, TEST_END))
        all_mf.append(_load_mf(conn, batch, DATA_START, TEST_END))
        all_tl.append(_load_tl(conn, batch, DATA_START, TEST_END))
        if (i//BATCH+1)%2==0: print(f"  已加载{i+len(batch)}只...")
    conn.close()

    daily = pd.concat(all_daily, ignore_index=True).dropna(subset=['close','high','low','open']).query('close>0 and open>0 and low>0')
    basic = pd.concat(all_basic, ignore_index=True)
    mf    = pd.concat(all_mf, ignore_index=True)
    tl    = pd.concat(all_tl, ignore_index=True)

    # 市值前向填充
    basic = basic.sort_values(['ts_code','trade_date'])
    basic[['total_mv','circ_mv']] = basic.groupby('ts_code')[['total_mv','circ_mv']].ffill().bfill()
    basic_idx = basic.set_index(['ts_code','trade_date'])

    # 按ts_code分组
    daily_g  = {c:g.reset_index(drop=True) for c,g in daily.groupby('ts_code')}
    mf_g     = {c:g.reset_index(drop=True) for c,g in mf.groupby('ts_code')}
    tl_g     = {c:g.reset_index(drop=True) for c,g in tl.groupby('ts_code')}
    print(f"数据加载完成,耗时{time.time()-t0:.0f}秒。有效股:{len(daily_g)}")

    # ---- 交易日历 + 调仓日(每5个交易日) ----
    all_tdates = sorted(daily[daily['trade_date']>=TEST_START]['trade_date'].unique())
    rebal_dates = all_tdates[::5]
    print(f"调仓日:{len(rebal_dates)}个 ({rebal_dates[0]}~{rebal_dates[-1]})")

    # ---- 生成买入信号 ----
    print("\n生成买入信号...")
    model = lgb.Booster(model_file=MODEL_PATH)
    signals=[]
    for ri,rdate in enumerate(rebal_dates):
        if ri%20==0: print(f"  {ri}/{len(rebal_dates)}: {rdate}")
        feats_batch=[]; codes_batch=[]
        for code, g in daily_g.items():
            pos=g.index[g['trade_date']==rdate]
            if len(pos)==0: continue
            T=int(pos[0])
            if T<LOOKBACK+1 or T+1>=len(g): continue
            f=make_features(g,T,LOOKBACK,mf_g.get(code),tl_g.get(code),basic_idx,code,{})
            if f is None: continue
            vals=[f.get(k,np.nan) for k in FEATURE_NAMES]
            if any(v is None or (isinstance(v,float) and np.isnan(v)) for v in vals): continue
            feats_batch.append(vals); codes_batch.append((code,T+1))  # T+1买入
        if feats_batch:
            scores=model.predict(pd.DataFrame(feats_batch,columns=FEATURE_NAMES))
            for (code,entry_i),sc in zip(codes_batch,scores):
                if sc>=BUY_THR:
                    signals.append({'rebal_date':rdate,'code':code,'entry_i':entry_i,'score':sc})
    sig_df=pd.DataFrame(signals)
    print(f"总买入信号:{len(sig_df)}")
    if len(sig_df)==0:
        print("无信号,退出"); exit()
    sig_df.to_csv(f"{OUTDIR}/backtest_signals.csv",index=False)

    report=[]; 
    def log(s): print(s); report.append(s)

    # ---- 口径A: 单笔独立统计 ----
    log(f"\n{'='*60}\n口径A: 单笔独立统计\n{'='*60}")
    def simulate(g, entry_i, rule):
        if entry_i>=len(g): return None
        bp=g['open'].values[entry_i]
        if bp<=0: return None
        cls=g['close'].values; hi=g['high'].values; lo=g['low'].values
        tp=rule['tp']; sl=rule['sl']; mh=rule['maxhold']
        for d in range(1,mh+1):
            j=entry_i+d
            if j>=len(g): return (cls[-1]/bp-1-COST, d, 'data_end')
            if tp and hi[j]/bp-1>=tp: return (tp-COST, d, 'take_profit')
            if sl and lo[j]/bp-1<=sl: return (sl-COST, d, 'stop_loss')
        j=min(entry_i+mh,len(g)-1)
        return (cls[j]/bp-1-COST, mh, 'time_exit')

    rule_results={}
    for rname,rule in SELL_RULES.items():
        rets=[]; days=[]; reasons=[]
        for _,row in sig_df.iterrows():
            g=daily_g.get(row['code'])
            if g is None: continue
            res=simulate(g,int(row['entry_i']),rule)
            if res: rets.append(res[0]); days.append(res[1]); reasons.append(res[2])
        rets=np.array(rets)
        rule_results[rname]={'rets':rets,'days':days,'reasons':reasons}
        if len(rets)==0: continue
        win=(rets>0).mean(); avg=rets.mean(); med=np.median(rets)
        aw=rets[rets>0].mean() if (rets>0).any() else 0
        al=rets[rets<=0].mean() if (rets<=0).any() else 0
        pl=abs(aw/al) if al!=0 else 999
        rc={r:reasons.count(r) for r in set(reasons)}
        log(f"\n【{rname}】 信号数:{len(rets)}")
        log(f"  胜率:{win:.2%} | 平均收益:{avg:.2%} | 中位:{med:.2%}")
        log(f"  平均盈:{aw:.2%} 平均亏:{al:.2%} 盈亏比:{pl:.2f}")
        log(f"  平均持有:{np.mean(days):.1f}天")
        log(f"  退出原因: {rc}")
        bins=[-1,-0.2,-0.1,0,0.1,0.3,0.5,1.0,99]
        labels=['<-20%','-20~-10%','-10~0%','0~10%','10~30%','30~50%','50~100%','>100%']
        dist=pd.cut(rets,bins=bins,labels=labels).value_counts().sort_index()
        log("  收益分布:")
        for lab,cnt in dist.items(): log(f"    {lab}: {cnt}({cnt/len(rets)*100:.0f}%)")

    # ---- 口径B: 组合净值 ----
    log(f"\n{'='*60}\n口径B: 组合净值(100万,最多{MAX_HOLD_P}只,等权)\n{'='*60}")
    for rname,rule in SELL_RULES.items():
        cap=float(INIT_CAP); positions={}; nav_curve=[]; nav_dates=[]; peak=cap; max_dd=0
        for rdate in rebal_dates:
            # 结算持仓(检查止盈/止损/到期)
            to_close=[]
            for code,pos in positions.items():
                g=daily_g.get(code)
                if g is None: to_close.append(code); continue
                cur=g.index[g['trade_date']==rdate]
                if len(cur)==0: continue
                ci=int(cur[0]); held=ci-pos['ei']
                bp=pos['bp']; cp=g['close'].values[ci]
                hi_since=g['high'].values[pos['ei']:ci+1].max() if ci>pos['ei'] else cp
                lo_now=g['low'].values[ci]
                exit_now=False; exit_ret=cp/bp-1
                if rule['tp'] and hi_since/bp-1>=rule['tp']: exit_now=True; exit_ret=rule['tp']
                elif rule['sl'] and lo_now/bp-1<=rule['sl']: exit_now=True; exit_ret=rule['sl']
                elif held>=rule['maxhold']: exit_now=True
                if exit_now:
                    cap+=pos['sh']*bp*(1+exit_ret-COST); to_close.append(code)
            for c in to_close: positions.pop(c,None)
            # 买入新信号
            slots=MAX_HOLD_P-len(positions)
            if slots>0:
                day_sig=sig_df[sig_df['rebal_date']==rdate].sort_values('score',ascending=False)
                budget=cap/max(slots,1)
                for _,s in day_sig.iterrows():
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
                    cost=sh*bp*(1+COST/2)
                    if cost>cap: continue
                    cap-=cost; positions[code]={'ei':ei,'bp':bp,'sh':sh}
            # 计算净值
            mv=cap
            for code,pos in positions.items():
                g=daily_g.get(code)
                if g is None: continue
                cur=g.index[g['trade_date']==rdate]
                if len(cur)>0: mv+=pos['sh']*float(g['close'].values[int(cur[0])])
            nav_curve.append(mv); nav_dates.append(rdate)
            peak=max(peak,mv); dd=(mv-peak)/peak; max_dd=min(max_dd,dd)
        nav=np.array(nav_curve)
        total=nav[-1]/INIT_CAP-1
        d0=datetime.strptime(rebal_dates[0],'%Y%m%d'); d1=datetime.strptime(rebal_dates[-1],'%Y%m%d')
        yrs=(d1-d0).days/365
        ann=(nav[-1]/INIT_CAP)**(1/yrs)-1 if yrs>0 else 0
        rs=np.diff(nav)/nav[:-1]; sh=rs.mean()/rs.std()*np.sqrt(50) if rs.std()>0 else 0
        log(f"\n【{rname}】")
        log(f"  期末净值:{nav[-1]:,.0f} 总收益:{total:.2%} 年化:{ann:.2%}")
        log(f"  最大回撤:{max_dd:.2%} 夏普:{sh:.2f} | 期末持仓:{len(positions)}只")
        pd.DataFrame({'date':nav_dates,'nav':nav_curve}).to_csv(f"{OUTDIR}/nav_{rname[:1]}.csv",index=False)

    with open(f"{OUTDIR}/backtest_report.txt",'w') as fp: fp.write("\n".join(report))
    print(f"\n回测完成! 耗时{(time.time()-t0)/60:.1f}分钟")
    print("报告: backtest_report.txt, 信号: backtest_signals.csv")
