#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S009 止损线敏感性测试：8% / 10% / 12% / 15% 四档 + 无止损基线
复用 trades_full.csv 已有选股结果，只在收益计算加止损逻辑。
每档输出：累计/年化/最大回撤/夏普/止损笔数/逐年，找收益-回撤平衡最优档。
"""
import json, sqlite3, time, ast
import numpy as np, pandas as pd

BASE = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股"
DB = "/Users/ziruzhu/stock-data/stock_all.db"
STOP_LEVELS = [0.08, 0.10, 0.12, 0.15]
BUY_C, SELL_C, STAMP = 0.00025, 0.00025, 0.0005

def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

log("读选股结果+预加载日线...")
t = pd.read_csv(f"{BASE}/trades_full.csv")
all_codes = set()
for _, r in t.iterrows():
    all_codes.update(ast.literal_eval(r["holdings"]))
mn, mx = str(t["buy_date"].min()), str(t["sell_date"].max())
con = sqlite3.connect(DB)
cs = ",".join([f"'{c}'" for c in all_codes])
px = pd.read_sql(f"SELECT ts_code,trade_date,open_qfq,close_qfq FROM stk_factor WHERE trade_date BETWEEN '{mn}' AND '{mx}' AND ts_code IN ({cs})", con)
con.close()
px["open_qfq"]=pd.to_numeric(px["open_qfq"],errors="coerce")
px["close_qfq"]=pd.to_numeric(px["close_qfq"],errors="coerce")
px=px.sort_values(["ts_code","trade_date"])
open_lk=px.set_index(["ts_code","trade_date"])["open_qfq"]
close_by={}
for code,sub in px.groupby("ts_code"):
    close_by[code]=(sub["trade_date"].values, sub["close_qfq"].values)
log(f"日线 {len(px):,} 行, {len(all_codes)} 股")

def sim(code, bd, sd, stop_pct):
    """单笔收益, stop_pct=None 则无止损。返回(ret, stopped)"""
    try: p0=open_lk.loc[(code,bd)]
    except KeyError: return None,False
    if pd.isna(p0) or p0<=0: return None,False
    if stop_pct is not None and code in close_by:
        sl=p0*(1-stop_pct)
        dates,closes=close_by[code]
        mask=(dates>bd)&(dates<=sd)
        for d,c in zip(dates[mask], closes[mask]):
            if pd.notna(c) and c<=sl:
                return float(c)/float(p0)-1, True
    try:
        p1=open_lk.loc[(code,sd)]
        if pd.isna(p1) or p1<=0: return None,False
        return float(p1)/float(p0)-1, False
    except KeyError: return None,False

def backtest(stop_pct):
    nav=1.0; prs=[]; navc=[]; prev=set(); n_stop=0; year_r={}; year_s={}
    for _,r in t.iterrows():
        codes=ast.literal_eval(r["holdings"]); bd=str(r["buy_date"]); sd=str(r["sell_date"])
        rets=[]; valid=[]; ns=0
        for c in codes:
            ret,stp=sim(c,bd,sd,stop_pct)
            if ret is None: continue
            rets.append(ret); valid.append(c)
            if stp: ns+=1
        if not rets: continue
        n_stop+=ns
        gross=float(np.mean(rets)); curr=set(valid)
        bt=len(curr-prev)/(len(curr) or 1); st=len(prev-curr)/(len(prev) or 1) if prev else 0
        extra=(ns/(len(curr) or 1))*(SELL_C+STAMP)
        cost=bt*BUY_C+st*(SELL_C+STAMP)+extra
        pr=gross-cost; nav*=(1+pr); prs.append(pr)
        navc.append(nav)
        yr=str(r["rebalance_date"])[:4]
        year_r.setdefault(yr,[]).append(pr); year_s[yr]=year_s.get(yr,0)+ns
        prev=curr
    pr=np.array(prs); n=len(pr); ny=n/12.0
    ann=(nav**(1/ny)-1) if ny>0 and nav>0 else 0
    navs=np.array([1.0]+navc); dd=float((navs/np.maximum.accumulate(navs)-1).min())
    sharpe=float(pr.mean()/pr.std()*np.sqrt(12)) if n>1 and pr.std()>0 else 0
    win=float(np.mean(pr>0))
    yearly={yr:round((np.prod([1+x for x in v])-1)*100,1) for yr,v in year_r.items()}
    return {"total":round((nav-1)*100,1),"annual":round(ann*100,2),"mdd":round(dd*100,2),
            "sharpe":round(sharpe,2),"win":round(win*100,1),"n_stop":n_stop,
            "yearly":yearly,"year_stops":year_s}

results={}
log("无止损基线...")
results["none"]=backtest(None)
for s in STOP_LEVELS:
    log(f"止损{int(s*100)}%...")
    results[f"{int(s*100)}%"]=backtest(s)

json.dump(results, open(f"{BASE}/stoploss_sensitivity_result.json","w"), ensure_ascii=False, indent=2)

# calmar = 年化/|最大回撤|，衡量单位回撤的收益效率
def calmar(r): return round(r["annual"]/abs(r["mdd"]),3) if r["mdd"]!=0 else 0

print("\n"+"="*78)
print(f"{'档位':<10}{'累计收益':>12}{'年化':>9}{'最大回撤':>11}{'夏普':>8}{'Calmar':>9}{'止损笔数':>9}")
print("-"*78)
order=["none","8%","10%","12%","15%"]
names={"none":"无止损","8%":"止损8%","10%":"止损10%","12%":"止损12%","15%":"止损15%"}
for k in order:
    r=results[k]
    print(f"{names[k]:<9}{r['total']:>11.0f}%{r['annual']:>8.1f}%{r['mdd']:>10.1f}%{r['sharpe']:>8.2f}{calmar(r):>9.3f}{r['n_stop']:>9}")
print("="*78)
print("\n逐年收益对比(%):")
yrs=sorted(results["none"]["yearly"].keys())
print(f"{'年份':<7}"+"".join(f"{names[k]:>10}" for k in order))
for yr in yrs:
    print(f"{yr:<7}"+"".join(f"{results[k]['yearly'].get(yr,0):>+9.1f}%" for k in order))
print("\n注: Calmar = 年化收益/|最大回撤|, 越高说明单位回撤换来的收益越高(风险调整后效率)")
