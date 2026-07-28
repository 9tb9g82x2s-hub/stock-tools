"""
交易回测引擎 - 用训练好的模型做事件驱动回测
两个口径:
  A) 单笔独立统计: 每个买入信号一笔,统计胜率/平均收益/盈亏比/收益分布
  B) 组合净值: 100万本金,最多持N只,等权,每周轮动,算年化/最大回撤/夏普
4套卖出规则对比。含交易成本。严格防未来函数(T+1开盘买入)。
"""
import sqlite3, pandas as pd, numpy as np, lightgbm as lgb, os
from datetime import datetime
import warnings; warnings.filterwarnings('ignore')
from config import DB_PATH, LOOKBACK, TEST_START, END_DATE, EXCLUDE_BJ
from features import make_features, FEATURE_NAMES

MODEL_PATH = "/Users/ziruzhu/stock-tools/strategy-lab/plan2_studio/plan2_model.txt"
OUTDIR = "/Users/ziruzhu/stock-tools/strategy-lab/plan2_studio"
BUY_THR = 0.5          # 买入信号阈值
COST_ONEWAY = 0.0015   # 单边成本(佣金+滑点),买卖双边约0.3%
STAMP = 0.0005         # 卖出印花税
INIT_CAP = 1_000_000   # 组合初始资金
MAX_HOLD = 10          # 组合最多持仓数
CAP_TEST_START = "20240101"
CAP_TEST_END   = "20260630"
FWD_MAX_HOLD   = 90    # 最长持有期(数据充足性检查用)

# 4套卖出规则
SELL_RULES = {
    'A_翻倍止盈+止损20+到期60': {'tp':1.0,'sl':-0.20,'maxhold':60},
    'B_固定持有60天':          {'tp':None,'sl':None,'maxhold':60},
    'C_止盈30+止损15+到期60':  {'tp':0.30,'sl':-0.15,'maxhold':60},
    'D_止盈50+止损20+到期90':  {'tp':0.50,'sl':-0.20,'maxhold':90},
}

def load_stock_data(conn, code):
    """加载单票全周期数据(到END_DATE,供回测持有期用)"""
    g=pd.read_sql(f"SELECT ts_code,trade_date,open,high,low,close,vol,amount FROM daily "
                  f"WHERE ts_code=? AND trade_date>='20160101' AND trade_date<='{END_DATE}' ORDER BY trade_date",
                  conn, params=[code])
    for c in ['open','high','low','close','vol','amount']: g[c]=pd.to_numeric(g[c],errors='coerce')
    g=g.dropna(subset=['close','high','low','open']).query('close>0 and low>0 and open>0').reset_index(drop=True)
    return g

def simulate_trade(g, entry_i, rule):
    """模拟单笔:entry_i为信号次日(买入日),按规则卖出,返回(收益率,持有天数,卖出原因)"""
    if entry_i>=len(g): return None
    buy_price=g['open'].values[entry_i]  # T+1开盘买入
    if buy_price<=0: return None
    close=g['close'].values; high=g['high'].values; low=g['low'].values
    tp=rule['tp']; sl=rule['sl']; mh=rule['maxhold']
    for d in range(1, mh+1):
        j=entry_i+d
        if j>=len(g):
            # 数据到头,按最后收盘价平仓
            ret=close[-1]/buy_price-1; return (ret-COST_ONEWAY*2-STAMP, d, 'data_end')
        # 先判止盈止损(用当日high/low)
        if tp is not None and high[j]/buy_price-1>=tp:
            ret=tp; return (ret-COST_ONEWAY*2-STAMP, d, 'take_profit')
        if sl is not None and low[j]/buy_price-1<=sl:
            ret=sl; return (ret-COST_ONEWAY*2-STAMP, d, 'stop_loss')
    # 到期按收盘卖
    j=min(entry_i+mh, len(g)-1)
    ret=close[j]/buy_price-1
    return (ret-COST_ONEWAY*2-STAMP, mh, 'time_exit')

if __name__=='__main__':
    print("加载模型+数据...")
    model=lgb.Booster(model_file=MODEL_PATH)
    conn=sqlite3.connect(DB_PATH)
    all_codes=pd.read_sql("SELECT DISTINCT ts_code FROM daily",conn)['ts_code'].tolist()
    if EXCLUDE_BJ: all_codes=[c for c in all_codes if not c.endswith('.BJ')]
    # 铁律:剔除ST+亏损
    bl=set(pd.read_sql("SELECT ts_code FROM blacklist_st",conn)['ts_code'])|set(pd.read_sql("SELECT ts_code FROM blacklist_loss",conn)['ts_code'])
    all_codes=[c for c in all_codes if c not in bl]
    print(f"股票池:{len(all_codes)}只(已剔除ST+亏损)")

    # 交易日历(测试期内每周一)
    tdates=pd.read_sql(f"SELECT DISTINCT trade_date FROM daily WHERE trade_date>='{CAP_TEST_START}' AND trade_date<='{CAP_TEST_END}' ORDER BY trade_date",conn)['trade_date'].tolist()
    # 每5个交易日取一个作为调仓日(近似每周)
    rebal_dates=tdates[::5]
    print(f"调仓日:{len(rebal_dates)}个 ({rebal_dates[0]}~{rebal_dates[-1]})")

    # ===== 生成所有调仓日的买入信号 =====
    print("\n生成买入信号(每个调仓日全市场打分)...")
    # 预加载所有票数据到内存字典(测试期回测需要,但只保留必要列)
    stock_data={}
    for code in all_codes:
        g=load_stock_data(conn, code)
        if len(g)>=LOOKBACK+FWD_MAX_HOLD+2 and len(stock_data)<100:
            stock_data[code]=g
    print(f"  有效股票:{len(stock_data)}只")

    # basic/mf/tl 预加载(用于特征)
    def load_aux(table, cols, code):
        q=f"SELECT ts_code,trade_date,{cols} FROM {table} WHERE ts_code=? AND trade_date>='20160101' AND trade_date<='{END_DATE}' ORDER BY trade_date"
        return pd.read_sql(q, conn, params=[code])

    signals=[]  # (rebal_date, code, entry_i, score)
    for ri, rdate in enumerate(rebal_dates):
        if ri%10==0: print(f"  调仓日进度 {ri}/{len(rebal_dates)}: {rdate}")
        for code, g in stock_data.items():
            pos=g.index[g['trade_date']==rdate]
            if len(pos)==0: continue
            T=int(pos[0])
            if T<LOOKBACK+1 or T+1>=len(g): continue
            # 构造特征(需要basic/mf/tl)
            b=load_aux('daily_basic','turnover_rate,pe_ttm,pb,ps_ttm,total_mv,circ_mv',code)
            for c in ['turnover_rate','pe_ttm','pb','ps_ttm','total_mv','circ_mv']: b[c]=pd.to_numeric(b[c],errors='coerce')
            b=b.sort_values('trade_date'); b[['total_mv','circ_mv']]=b[['total_mv','circ_mv']].ffill().bfill()
            b_idx=b.set_index(['ts_code','trade_date'])
            mfg=load_aux('moneyflow','buy_elg_amount,sell_elg_amount,buy_lg_amount,sell_lg_amount,net_mf_amount',code)
            for c in ['buy_elg_amount','sell_elg_amount','buy_lg_amount','sell_lg_amount','net_mf_amount']: mfg[c]=pd.to_numeric(mfg[c],errors='coerce')
            mfg['elg_net']=mfg['buy_elg_amount']-mfg['sell_elg_amount']; mfg['lg_net']=mfg['buy_lg_amount']-mfg['sell_lg_amount']
            tlg=load_aux('top_list','net_amount',code); tlg['net_amount']=pd.to_numeric(tlg['net_amount'],errors='coerce')
            f=make_features(g,T,LOOKBACK,mfg,tlg,b_idx,code,{})
            if f is None: continue
            vals=[f.get(k,np.nan) for k in FEATURE_NAMES]
            if any(v is None or (isinstance(v,float) and np.isnan(v)) for v in vals): continue
            score=model.predict(pd.DataFrame([vals],columns=FEATURE_NAMES))[0]
            if score>=BUY_THR:
                signals.append((rdate, code, T+1, score))  # T+1买入
    conn.close()
    sig_df=pd.DataFrame(signals,columns=['rebal_date','code','entry_i','score'])
    print(f"\n总买入信号:{len(sig_df)}个")
    sig_df.to_csv(f"{OUTDIR}/backtest_signals.csv",index=False)

    # ===== 口径A:单笔独立统计(每套规则) =====
    print(f"\n{'='*60}\n口径A: 单笔独立统计\n{'='*60}")
    report_lines=[]
    def log(s): print(s); report_lines.append(s)

    for rname, rule in SELL_RULES.items():
        rets=[]; days=[]; reasons=[]
        for _,row in sig_df.iterrows():
            g=stock_data[row['code']]
            res=simulate_trade(g, int(row['entry_i']), rule)
            if res: rets.append(res[0]); days.append(res[1]); reasons.append(res[2])
        rets=np.array(rets)
        if len(rets)==0: continue
        win=(rets>0).mean()
        avg_ret=rets.mean(); med_ret=np.median(rets)
        avg_win=rets[rets>0].mean() if (rets>0).any() else 0
        avg_loss=rets[rets<=0].mean() if (rets<=0).any() else 0
        pl_ratio=abs(avg_win/avg_loss) if avg_loss!=0 else np.inf
        log(f"\n【{rname}】 信号数:{len(rets)}")
        log(f"  胜率:{win:.2%} | 平均收益:{avg_ret:.2%} | 中位:{med_ret:.2%}")
        log(f"  平均盈:{avg_win:.2%} 平均亏:{avg_loss:.2%} 盈亏比:{pl_ratio:.2f}")
        log(f"  平均持有:{np.mean(days):.1f}天 | 最大单笔:{rets.max():.2%} 最小:{rets.min():.2%}")
        # 收益分布
        bins=[-1,-0.2,-0.1,0,0.1,0.3,0.5,1.0,100]
        labels=['<-20%','-20~-10%','-10~0%','0~10%','10~30%','30~50%','50~100%','>100%']
        dist=pd.cut(rets,bins=bins,labels=labels).value_counts().sort_index()
        log("  收益分布:")
        for lab,cnt in dist.items():
            log(f"    {lab}: {cnt} ({cnt/len(rets)*100:.1f}%)")

    # ===== 口径B:组合净值 =====
    log(f"\n{'='*60}\n口径B: 组合净值(100万,最多{MAX_HOLD}只,等权,每周轮动)\n{'='*60}")
    for rname, rule in SELL_RULES.items():
        # 简化组合模拟:每个调仓日,选当日信号分数最高的票,补齐到MAX_HOLD只
        cap=INIT_CAP; positions={}; nav_curve=[]; nav_dates=[]
        peak=cap; max_dd=0
        for rdate in rebal_dates:
            # 先结算到期/触发止盈止损的持仓
            to_close=[]
            for code,pos in positions.items():
                g=stock_data[code]; cur=g.index[g['trade_date']==rdate]
                if len(cur)==0: continue
                ci=int(cur[0]); held=ci-pos['entry_i']
                bp=pos['buy_price']; cp=g['close'].values[ci]
                hi=g['high'].values[max(pos['entry_i'],ci-5):ci+1].max() if ci>pos['entry_i'] else cp
                exit_now=False; exit_ret=cp/bp-1
                if rule['tp'] and hi/bp-1>=rule['tp']: exit_now=True; exit_ret=rule['tp']
                elif rule['sl'] and g['low'].values[ci]/bp-1<=rule['sl']: exit_now=True; exit_ret=rule['sl']
                elif held>=rule['maxhold']: exit_now=True
                if exit_now:
                    cap+=pos['shares']*bp*(1+exit_ret)*(1-COST_ONEWAY-STAMP)
                    to_close.append(code)
            for c in to_close: del positions[c]
            # 买入新信号(补齐到MAX_HOLD)
            day_sig=sig_df[sig_df['rebal_date']==rdate].sort_values('score',ascending=False)
            slots=MAX_HOLD-len(positions)
            if slots>0 and len(day_sig)>0:
                budget=cap/slots if slots>0 else 0
                for _,s in day_sig.head(slots).iterrows():
                    code=s['code']
                    if code in positions: continue
                    g=stock_data[code]; ei=int(s['entry_i'])
                    if ei>=len(g): continue
                    bp=g['open'].values[ei]
                    if bp<=0 or budget<bp*100: continue
                    shares=int(budget/bp/100)*100
                    if shares<100: continue
                    cost=shares*bp*(1+COST_ONEWAY)
                    if cost>cap: continue
                    cap-=cost
                    positions[code]={'entry_i':ei,'buy_price':bp,'shares':shares}
            # 计算当前净值(现金+持仓市值)
            mv=cap
            for code,pos in positions.items():
                g=stock_data[code]; cur=g.index[g['trade_date']==rdate]
                if len(cur)>0: mv+=pos['shares']*g['close'].values[int(cur[0])]
            nav_curve.append(mv); nav_dates.append(rdate)
            peak=max(peak,mv); dd=(mv-peak)/peak; max_dd=min(max_dd,dd)
        nav=np.array(nav_curve)
        total_ret=nav[-1]/INIT_CAP-1
        years=(datetime.strptime(rebal_dates[-1],'%Y%m%d')-datetime.strptime(rebal_dates[0],'%Y%m%d')).days/365
        ann_ret=(nav[-1]/INIT_CAP)**(1/years)-1 if years>0 else 0
        rets_series=np.diff(nav)/nav[:-1]
        sharpe=rets_series.mean()/rets_series.std()*np.sqrt(50) if rets_series.std()>0 else 0
        log(f"\n【{rname}】")
        log(f"  期末净值:{nav[-1]:,.0f} | 总收益:{total_ret:.2%} | 年化:{ann_ret:.2%}")
        log(f"  最大回撤:{max_dd:.2%} | 夏普:{sharpe:.2f} | 期末持仓:{len(positions)}只")
        pd.DataFrame({'date':nav_dates,'nav':nav_curve}).to_csv(f"{OUTDIR}/nav_{rname[:1]}.csv",index=False)

    with open(f"{OUTDIR}/backtest_report.txt",'w') as fp:
        fp.write("\n".join(report_lines))
    print(f"\n回测报告已存 backtest_report.txt")
