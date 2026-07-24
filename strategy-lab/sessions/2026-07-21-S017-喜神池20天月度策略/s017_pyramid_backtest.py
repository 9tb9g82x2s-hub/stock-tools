# -*- coding: utf-8 -*-
"""
S017 "跟随加码"回测模块
思路：不预测牛股，而是持有期第K天观察组合内涨幅，对领涨票追加权重(额外资金)。
两段式建模：
  段1  买入日开盘 -> 第K天收盘 (等权)
  段2  第K天收盘再平衡 -> 期末开盘卖 (领涨票加权)
全程 -12% 止损(相对entry收盘)，计入交易成本。
K=0 即原等权策略(基准)。
"""
import sqlite3, json, pandas as pd, numpy as np
from itertools import product

RES = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-21-S017-喜神池20天月度策略/s017_result.json"
DB  = "/Users/ziruzhu/stock-data/stock_all.db"
STOP_LOSS = -0.12
BUY_C, SELL_C, STAMP = 0.00025, 0.00025, 0.0005

d = json.load(open(RES)); trades = d['trades']
con = sqlite3.connect(DB)
codes = set()
for t in trades: codes.update(t['holdings'])
PX = {}
for c in codes:
    df = pd.read_sql("SELECT trade_date, open_qfq, close_qfq FROM stk_factor WHERE ts_code=? ORDER BY trade_date", con, params=(c,))
    df['open_qfq']=pd.to_numeric(df['open_qfq'],errors='coerce')
    df['close_qfq']=pd.to_numeric(df['close_qfq'],errors='coerce')
    df=df.dropna(subset=['open_qfq','close_qfq']).reset_index(drop=True)
    PX[c]={'dates':df['trade_date'].tolist(),
           'o':df['open_qfq'].values,'c':df['close_qfq'].values}
con.close()

def leg_data(code, buy_date, sell_date, K):
    """返回该票 entry, r1(段1收益), r2(段2收益), stop_seg(1/2/None), pK有效标记"""
    p = PX.get(code)
    if p is None or buy_date not in p['dates']: return None
    dates=p['dates']; bi=dates.index(buy_date)
    entry=p['o'][bi]
    if pd.isna(entry) or entry<=0: return None
    si = dates.index(sell_date) if sell_date in dates else len(dates)-1
    if si<=bi: return None
    Keff = K if K>0 else 0
    kidx = bi+Keff
    if K>0 and kidx>=si:   # 持有期太短放不下段1，退化为不加码
        kidx = None
    # 扫描止损(相对entry收盘)
    stop_idx=None
    for i in range(bi+1, si):
        cl=p['c'][i]
        if not pd.isna(cl) and cl>0 and cl/entry-1<=STOP_LOSS:
            stop_idx=i; break
    if K==0 or kidx is None:
        # 单段：entry->期末/止损
        if stop_idx is not None:
            r=p['c'][stop_idx]/entry-1
        else:
            ex=p['o'][si]; r=ex/entry-1 if (not pd.isna(ex) and ex>0) else None
        if r is None: return None
        return dict(entry=entry,r1=r,r2=0.0,stop_seg=(1 if stop_idx else None),split=False)
    # 两段
    pK=p['c'][kidx]
    if pd.isna(pK) or pK<=0: return None
    if stop_idx is not None and stop_idx<=kidx:
        r1=p['c'][stop_idx]/entry-1
        return dict(entry=entry,r1=r1,r2=0.0,stop_seg=1,split=True)
    r1=pK/entry-1
    if stop_idx is not None:
        r2=p['c'][stop_idx]/pK-1; stg=2
    else:
        ex=p['o'][si]
        if pd.isna(ex) or ex<=0: return None
        r2=ex/pK-1; stg=None
    return dict(entry=entry,r1=r1,r2=r2,stop_seg=stg,split=True)

# ---- 单期组合收益：等权段1 + 第K天领涨加权段2 ----
def period_return(t, K, mult, top_pct):
    legs=[]
    for c in t['holdings']:
        ld=leg_data(c,t['buy_date'],t['sell_date'],K)
        if ld is not None: legs.append((c,ld))
    n=len(legs)
    if n<4: return None
    w0=1.0/n
    v1={c:w0*(1+ld['r1']) for c,ld in legs}   # 段1末各票价值
    V_K=sum(v1.values())
    if K==0:
        gross=V_K-1
        cost=BUY_C + (SELL_C+STAMP)   # 全进全出
        return gross-cost
    # 段1未离场的票(可再平衡)
    active=[(c,ld) for c,ld in legs if ld['stop_seg']!=1]
    exited_val=sum(v1[c] for c,ld in legs if ld['stop_seg']==1)
    V_active=V_K-exited_val
    if not active or V_active<=0:
        gross=V_K-1; cost=BUY_C+(SELL_C+STAMP)
        return gross-cost
    # 按段1收益排名，前top_pct加权
    r1s=np.array([ld['r1'] for c,ld in active])
    thr=np.quantile(r1s, 1-top_pct)
    raw=np.array([mult if ld['r1']>=thr else 1.0 for c,ld in active])
    w1=raw/raw.sum()                     # 在场票目标权重(占V_active)
    # 段2
    end_active=sum(w1[i]*V_active*(1+active[i][1]['r2']) for i in range(len(active)))
    V_end=exited_val+end_active
    gross=V_end-1
    # 成本：初始买入 + 第K天再平衡换手 + 期末卖出
    wd=np.array([v1[c]/V_K for c,ld in active])          # 漂移权重(占总)
    wt=w1*V_active/V_K                                    # 目标权重(占总)
    turnover=np.abs(wt-wd).sum()/2
    cost=BUY_C + turnover*(BUY_C+SELL_C+STAMP) + (SELL_C+STAMP)
    return gross-cost

def run(K,mult,top_pct):
    rets=[]
    for t in trades:
        r=period_return(t,K,mult,top_pct)
        if r is not None: rets.append(r)
    r=np.array(rets); nav=np.prod(1+r)
    first=pd.to_datetime(trades[0]['buy_date'],format='%Y%m%d')
    last=pd.to_datetime(trades[-1]['sell_date'],format='%Y%m%d')
    yrs=(last-first).days/365.25
    navc=np.cumprod(1+r)
    dd=(navc/np.maximum.accumulate(navc)-1).min()
    ann=nav**(1/yrs)-1 if yrs>0 and nav>0 else 0
    sharpe=r.mean()/r.std()*np.sqrt(12) if r.std()>0 else 0
    return dict(K=K,mult=mult,top_pct=top_pct,n=len(r),
                ann=ann,nav=nav,dd=dd,sharpe=sharpe,
                win=(r>0).mean(),avg=r.mean(),navc=navc.tolist())

if __name__=="__main__":
    base=run(0,1.0,0.25)
    print("="*90)
    print("基准(原等权,K=0): 年化%.1f%%  净值%.1fx  回撤%.1f%%  夏普%.2f  胜率%.1f%%" % (
        base['ann']*100,base['nav'],base['dd']*100,base['sharpe'],base['win']*100))
    print("="*90)
    print("%-4s %-6s %-7s %6s %8s %8s %7s %7s %8s" % ("K天","倍数","档位","期数","年化","净值x","回撤","夏普","胜率"))
    print("-"*90)
    grid=[]
    for K,mult,tp in product([3,5,8],[1.5,2.0,3.0],[0.10,0.25]):
        res=run(K,mult,tp)
        grid.append(res)
        print("%-4d %-6.1f %-7s %6d %7.1f%% %7.1fx %6.1f%% %7.2f %7.1f%%" % (
            res['K'],res['mult'],"前%d%%"%int(tp*100),res['n'],
            res['ann']*100,res['nav'],res['dd']*100,res['sharpe'],res['win']*100))
    # 存全部结果供报告
    out={'base':{k:v for k,v in base.items()},
         'grid':[{k:v for k,v in g.items()} for g in grid]}
    json.dump(out, open("/Users/ziruzhu/stock-data/s017_pyramid_result.json","w"),
              ensure_ascii=False, default=float)
    print("-"*90)
    best=max(grid,key=lambda x:x['sharpe'])
    print("按夏普最优: K=%d 倍数%.1f 档位前%d%% -> 年化%.1f%% 净值%.1fx 回撤%.1f%% 夏普%.2f" % (
        best['K'],best['mult'],int(best['top_pct']*100),
        best['ann']*100,best['nav'],best['dd']*100,best['sharpe']))
    best2=max(grid,key=lambda x:x['ann'])
    print("按年化最优: K=%d 倍数%.1f 档位前%d%% -> 年化%.1f%% 净值%.1fx 回撤%.1f%% 夏普%.2f" % (
        best2['K'],best2['mult'],int(best2['top_pct']*100),
        best2['ann']*100,best2['nav'],best2['dd']*100,best2['sharpe']))
