#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
止损口径三方对比 × 止损线四档
三种口径：
  C1 收盘价止损(当前版)   : 收盘 <= 止损线 触发, 按当日收盘价出场 [偏乐观]
  C2 次日开盘止损(实盘手动): 收盘 <= 止损线 触发, 按次日开盘价出场 [含隔夜跳空, 最贴近老大手动操作]
  C3 盘中最低价止损(挂单)  : 盘中最低 <= 止损线 触发, 按止损线价出场 [最保守, 模拟自动挂单]
止损线: 8%/10%/12%/15%
复用 trades_full.csv 选股结果, 不重训模型。
"""
import json, sqlite3, time, ast
import numpy as np, pandas as pd

BASE = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股"
DB = "/Users/ziruzhu/stock-data/stock_all.db"
STOP_LEVELS = [0.08, 0.10, 0.12, 0.15]
BUY_C, SELL_C, STAMP = 0.00025, 0.00025, 0.0005

def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

log("读选股结果+预加载日线(open/close/low + 全交易日序列)...")
t = pd.read_csv(f"{BASE}/trades_full.csv")
all_codes=set()
for _,r in t.iterrows(): all_codes.update(ast.literal_eval(r["holdings"]))
mn,mx=str(t["buy_date"].min()),str(t["sell_date"].max())
con=sqlite3.connect(DB)
cs=",".join([f"'{c}'" for c in all_codes])
px=pd.read_sql(f"SELECT ts_code,trade_date,open_qfq,close_qfq,low_qfq FROM stk_factor WHERE trade_date BETWEEN '{mn}' AND '{mx}' AND ts_code IN ({cs})",con)
# 全市场交易日序列(用于找"次日")
alltd=pd.read_sql(f"SELECT DISTINCT trade_date FROM stk_factor WHERE trade_date BETWEEN '{mn}' AND '{mx}'",con)["trade_date"].tolist()
con.close()
for col in ["open_qfq","close_qfq","low_qfq"]:
    px[col]=pd.to_numeric(px[col],errors="coerce")
px=px.sort_values(["ts_code","trade_date"])
alltd=sorted(alltd)
next_map={d:alltd[i+1] for i,d in enumerate(alltd) if i+1<len(alltd)}

open_lk=px.set_index(["ts_code","trade_date"])["open_qfq"]
# 每股: 日期数组 + close/low 数组
arr_by={}
for code,sub in px.groupby("ts_code"):
    arr_by[code]=(sub["trade_date"].values, sub["close_qfq"].values, sub["low_qfq"].values,
                  dict(zip(sub["trade_date"].values, sub["open_qfq"].values)))
log(f"日线 {len(px):,} 行, {len(all_codes)} 股")

def sim(code, bd, sd, stop_pct, mode):
    """mode: 'close'/'nextopen'/'low'; 返回(ret, stopped)"""
    try: p0=open_lk.loc[(code,bd)]
    except KeyError: return None,False
    if pd.isna(p0) or p0<=0: return None,False
    if stop_pct is not None and code in arr_by:
        sl=p0*(1-stop_pct)
        dates,closes,lows,opens=arr_by[code]
        mask=(dates>bd)&(dates<=sd)
        for d,c,lw in zip(dates[mask],closes[mask],lows[mask]):
            if mode=="close":
                if pd.notna(c) and c<=sl:
                    return float(c)/float(p0)-1, True   # 当日收盘价出场
            elif mode=="nextopen":
                if pd.notna(c) and c<=sl:
                    nd=next_map.get(d)
                    if nd and nd<=sd and nd in opens and pd.notna(opens[nd]) and opens[nd]>0:
                        return float(opens[nd])/float(p0)-1, True   # 次日开盘价出场
                    # 次日超出持有期或无数据: 用当日收盘价近似
                    return float(c)/float(p0)-1, True
            elif mode=="low":
                if pd.notna(lw) and lw<=sl:
                    return float(sl)/float(p0)-1, True   # 盘中触发, 按止损线价成交(-stop_pct)
    try:
        p1=open_lk.loc[(code,sd)]
        if pd.isna(p1) or p1<=0: return None,False
        return float(p1)/float(p0)-1, False
    except KeyError: return None,False

def backtest(stop_pct, mode):
    nav=1.0; prs=[]; navc=[]; prev=set(); n_stop=0; year_r={}
    for _,r in t.iterrows():
        codes=ast.literal_eval(r["holdings"]); bd=str(r["buy_date"]); sd=str(r["sell_date"])
        rets=[]; valid=[]; ns=0
        for c in codes:
            ret,stp=sim(c,bd,sd,stop_pct,mode)
            if ret is None: continue
            rets.append(ret); valid.append(c)
            if stp: ns+=1
        if not rets: continue
        n_stop+=ns
        gross=float(np.mean(rets)); curr=set(valid)
        bt=len(curr-prev)/(len(curr) or 1); st=len(prev-curr)/(len(prev) or 1) if prev else 0
        extra=(ns/(len(curr) or 1))*(SELL_C+STAMP)
        cost=bt*BUY_C+st*(SELL_C+STAMP)+extra
        pr=gross-cost; nav*=(1+pr); prs.append(pr); navc.append(nav)
        yr=str(r["rebalance_date"])[:4]; year_r.setdefault(yr,[]).append(pr)
        prev=curr
    pr=np.array(prs); n=len(pr); ny=n/12.0
    ann=(nav**(1/ny)-1) if ny>0 and nav>0 else 0
    navs=np.array([1.0]+navc); dd=float((navs/np.maximum.accumulate(navs)-1).min())
    sharpe=float(pr.mean()/pr.std()*np.sqrt(12)) if n>1 and pr.std()>0 else 0
    yearly={yr:round((np.prod([1+x for x in v])-1)*100,1) for yr,v in year_r.items()}
    return {"total":round((nav-1)*100,1),"annual":round(ann*100,2),"mdd":round(dd*100,2),
            "sharpe":round(sharpe,2),"n_stop":n_stop,"yearly":yearly}

modes=[("close","C1收盘价止损"),("nextopen","C2次日开盘止损"),("low","C3盘中最低价止损")]
results={}
# 基线(无止损)
base=backtest(None,"close")
results["baseline"]=base
log(f"无止损基线: 累计{base['total']}% 年化{base['annual']}% 回撤{base['mdd']}%")
for mkey,mname in modes:
    for s in STOP_LEVELS:
        results[f"{mkey}_{int(s*100)}"]=backtest(s,mkey)
    log(f"{mname} 四档跑完")

json.dump(results, open(f"{BASE}/stoploss_3modes_result.json","w"), ensure_ascii=False, indent=2)

def cal(r): return round(r["annual"]/abs(r["mdd"]),2) if r["mdd"]!=0 else 0
print("\n"+"="*82)
print("无止损基线: 累计%d%%  年化%.1f%%  回撤%.1f%%  夏普%.2f  Calmar%.2f" % (base["total"],base["annual"],base["mdd"],base["sharpe"],cal(base)))
print("="*82)
for mkey,mname in modes:
    print(f"\n【{mname}】")
    print(f"{'止损线':<8}{'累计收益':>11}{'年化':>9}{'最大回撤':>10}{'夏普':>8}{'Calmar':>9}{'止损笔数':>9}")
    print("-"*64)
    for s in STOP_LEVELS:
        r=results[f"{mkey}_{int(s*100)}"]
        print(f"{str(int(s*100))+'%':<8}{r['total']:>10.0f}%{r['annual']:>8.1f}%{r['mdd']:>9.1f}%{r['sharpe']:>8.2f}{cal(r):>9.2f}{r['n_stop']:>9}")

# 重点对比: 12%止损三口径
print("\n"+"="*82)
print("【重点】12%止损 三口径横向对比 (最能反映'回测理想 vs 实盘真实'差距)")
print(f"{'口径':<20}{'累计收益':>12}{'年化':>9}{'最大回撤':>11}{'夏普':>8}")
print("-"*60)
print(f"{'无止损基线':<19}{base['total']:>11.0f}%{base['annual']:>8.1f}%{base['mdd']:>10.1f}%{base['sharpe']:>8.2f}")
for mkey,mname in modes:
    r=results[f"{mkey}_12"]
    print(f"{mname:<18}{r['total']:>11.0f}%{r['annual']:>8.1f}%{r['mdd']:>10.1f}%{r['sharpe']:>8.2f}")
