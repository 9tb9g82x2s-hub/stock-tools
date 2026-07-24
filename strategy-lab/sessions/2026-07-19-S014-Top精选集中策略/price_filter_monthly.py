#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股价上限过滤（正确版）：在【打分候选池】阶段剔除高价股，再选满Top20
月度策略专用（S017喜神池 / S018全市场，20天label）

关键：用真实不复权收盘价(daily.close)判断股价，避免前复权失真
用法: python price_filter_monthly.py <s017|s018>
一次跑该策略的 4 个 cap [200,300,400,500] + 无上限，带断点续跑
"""
import json, sqlite3, time, os, sys, gc
import numpy as np, pandas as pd
import lightgbm as lgb

SOURCE = sys.argv[1] if len(sys.argv) > 1 else "s017"
if SOURCE == "s018":
    PANEL_PATH = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股/features_panel.pkl"
    USE_XISHEN = False
else:  # s017
    PANEL_PATH = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S013-喜神池LightGBM选股/features_panel.pkl"
    USE_XISHEN = True

OUT_DIR = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S014-Top精选集中策略"
DB_PATH = "/Users/ziruzhu/stock-data/stock_all.db"
XISHEN_PATH = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S013-喜神池LightGBM选股/xishen_plus_pool.csv"
LOG = f"{OUT_DIR}/pricefilter_{SOURCE}.log"
OUT_FILE = f"{OUT_DIR}/pricefilter_{SOURCE}_result.json"

HORIZON = 20
BACKTEST_START = "20170101"
TRAIN_MONTHS = 12
TOP_N = 20
STOP_LOSS = -0.12
BC, SC = 0.00025, 0.00125
CAPS = [99999, 500, 400, 300, 200]   # 99999=无上限(原版)

FEATURE_COLS = [
    "mom_5","mom_10","mom_20","mom_60","mom_120",
    "turnover_rate","turnover_rate_f","volume_ratio","vol_chg_20",
    "bias_5","bias_10","bias_20","bias_60",
    "macd_dif","macd_dea","macd","kdj_k","kdj_d","kdj_j",
    "rsi_6","rsi_12","rsi_24","cci","boll_pct","boll_width",
    "pe","pe_ttm","pb","ps","ps_ttm","dv_ttm",
    "net_mf_ratio","lg_buy_ratio",
]

def log(m):
    line=f"[{time.strftime('%H:%M:%S')}] {m}"; print(line,flush=True)
    with open(LOG,"a") as f: f.write(line+"\n")

def load_blacklist():
    con=sqlite3.connect(DB_PATH)
    st=pd.read_sql("SELECT ts_code FROM blacklist_st",con)["ts_code"].tolist()
    loss=pd.read_sql("SELECT ts_code FROM blacklist_loss",con)["ts_code"].tolist()
    con.close(); return set(st)|set(loss)

def get_month_start(trade_dates):
    s=pd.Series(pd.to_datetime(trade_dates,format="%Y%m%d"))
    df=pd.DataFrame({"date":s,"trade_date":trade_dates})
    df["ym"]=df["date"].dt.to_period("M")
    return df.groupby("ym").first()["trade_date"].tolist()

def main():
    t0=time.time()
    log("="*60); log(f"股价过滤回测(正确版): {SOURCE}, 打分池剔除高价股")

    if USE_XISHEN:
        xishen_set=set(pd.read_csv(XISHEN_PATH)["ts_code"]); log(f"喜神池{len(xishen_set)}只")
    else:
        xishen_set=None; log("全市场")

    log("加载面板+重算20天label...")
    panel=pd.read_pickle(PANEL_PATH)
    panel=panel.sort_values(["ts_code","trade_date"]).reset_index(drop=True)
    panel["fwd"]=panel.groupby("ts_code")["close_qfq"].transform(lambda s:s.shift(-HORIZON)/s-1)
    med=panel.groupby("trade_date")["fwd"].transform("median")
    panel["label"]=(panel["fwd"]>med).astype("Int8")
    panel.loc[panel["fwd"].isna(),"label"]=pd.NA
    panel.drop(columns=["fwd"],inplace=True); gc.collect()

    bl=load_blacklist()
    panel=panel[~panel["ts_code"].isin(bl)]
    panel=panel[~panel["ts_code"].str.endswith(".BJ")]
    panel=panel.dropna(subset=FEATURE_COLS,how="all").reset_index(drop=True)
    log(f"面板清洗后{len(panel):,}行")

    all_td=sorted(panel["trade_date"].unique())
    reb=[d for d in get_month_start(all_td) if d>=BACKTEST_START]
    panel_by_date={d:sub for d,sub in panel.groupby("trade_date")}
    open_lk=panel.set_index(["ts_code","trade_date"])["open_qfq"].sort_index()
    close_lk=panel.set_index(["ts_code","trade_date"])["close_qfq"].sort_index()
    next_td={d:all_td[i+1] for i,d in enumerate(all_td) if i+1<len(all_td)}
    log(f"调仓日{len(reb)}个")

    # 预加载所有调仓日的真实不复权收盘价（用于股价过滤）
    log("预加载调仓日真实收盘价...")
    conn=sqlite3.connect(DB_PATH)
    reb_ph=",".join(f"'{d}'" for d in reb)
    real_px=pd.read_sql(f"SELECT ts_code,trade_date,close FROM daily WHERE trade_date IN ({reb_ph})",conn)
    conn.close()
    real_px["close"]=pd.to_numeric(real_px["close"],errors="coerce")
    real_px_idx=real_px.set_index(["trade_date","ts_code"])["close"]

    def run_one_cap(cap):
        trades=[]; nav=1.0; prev=set()
        for i,rd in enumerate(reb):
            rd_dt=pd.to_datetime(rd,format="%Y%m%d")
            ts=(rd_dt-pd.DateOffset(months=TRAIN_MONTHS)).strftime("%Y%m%d")
            tr=panel.loc[(panel["trade_date"]>=ts)&(panel["trade_date"]<rd)].dropna(subset=FEATURE_COLS+["label"])
            if len(tr)<5000: continue
            sc=panel_by_date.get(rd)
            if sc is None or len(sc)==0: continue
            if USE_XISHEN:
                sc=sc[sc["ts_code"].isin(xishen_set)]
            sc=sc.dropna(subset=FEATURE_COLS,how="all").copy()
            # ★★★ 核心：打分前用真实价过滤高价股 ★★★
            if cap < 99999:
                try:
                    day_px=real_px_idx.loc[rd]  # 该调仓日所有股真实close
                    keep=sc["ts_code"].map(day_px)
                    sc=sc[(keep<=cap)&(keep.notna())]
                except KeyError:
                    pass
            if len(sc)<TOP_N: continue
            m=lgb.LGBMClassifier(boosting_type="gbdt",num_leaves=31,learning_rate=0.05,
                n_estimators=200,subsample=0.8,colsample_bytree=0.8,random_state=42,verbose=-1)
            m.fit(tr[FEATURE_COLS],tr["label"])
            sc["score"]=m.predict_proba(sc[FEATURE_COLS])[:,1]
            top=sc.sort_values("score",ascending=False).head(TOP_N)["ts_code"].tolist()
            nrd=reb[i+1] if i+1<len(reb) else None
            if nrd is None: break
            bd=next_td.get(rd); sd=next_td.get(nrd)
            if bd is None or sd is None: continue
            rets,vh=[],[]
            for c in top:
                try:
                    ep=open_lk.loc[(c,bd)]
                    if pd.isna(ep) or ep<=0: continue
                    hit,sp,d=False,None,bd
                    while d<sd:
                        dn=next_td.get(d)
                        if dn is None: break
                        try: cl=close_lk.loc[(c,dn)]
                        except KeyError: d=dn; continue
                        if not pd.isna(cl) and cl>0 and cl/ep-1<=STOP_LOSS: hit,sp=True,cl; break
                        d=dn
                    if hit: r=float(sp)/float(ep)-1
                    else:
                        try: xp=open_lk.loc[(c,sd)]
                        except KeyError: continue
                        if pd.isna(xp) or xp<=0: continue
                        r=float(xp)/float(ep)-1
                    rets.append(r); vh.append(c)
                except KeyError: continue
            if not rets: continue
            gr=float(np.mean(rets)); cur=set(vh)
            bt=len(cur-prev)/(len(cur) or 1); st=len(prev-cur)/(len(prev) or 1)
            cost=bt*BC+st*(SC+0.0005)
            nav*=(1+gr-cost)
            trades.append({"rebalance_date":rd,"buy_date":bd,"sell_date":sd,
                "period_return":round(gr-cost,6),"n":len(vh)})
            prev=cur
        pr=pd.Series([t["period_return"] for t in trades])
        navv=(1+pr).cumprod().iloc[-1]
        fd=pd.to_datetime(trades[0]["buy_date"]); ld=pd.to_datetime(trades[-1]["sell_date"])
        ny=(ld-fd).days/365.25
        ann=navv**(1/ny)-1; dd=float((((1+pr).cumprod()-((1+pr).cumprod().cummax()))/((1+pr).cumprod().cummax())).min())
        sh=float(pr.mean()/pr.std()*np.sqrt(12)) if pr.std()>0 else 0
        return {"cap":cap,"ann":round(ann*100,1),"dd":round(dd*100,1),"sharpe":round(sh,2),
                "wr":round((pr>0).mean()*100,1),"total":round((navv-1)*100,0),"n":len(trades)}

    results=[]
    for cap in CAPS:
        log(f"跑 cap={cap if cap<99999 else '无上限'} ...")
        r=run_one_cap(cap); results.append(r)
        cl="无上限" if cap>=99999 else f"≤{cap}元"
        log(f"  {cl}: 年化{r['ann']}% 回撤{r['dd']}% 夏普{r['sharpe']} 期数{r['n']}")
        json.dump(results,open(OUT_FILE,"w"),ensure_ascii=False,indent=2)

    log(f"\n{SOURCE} 完成，耗时{time.time()-t0:.0f}s")
    print(f"\n{'='*55}\n  {SOURCE} 股价过滤结果（打分池剔除高价股，选满Top20）\n{'='*55}")
    print(f"  {'股价上限':<10}{'年化':>8}{'回撤':>8}{'夏普':>7}{'胜率':>7}{'期数':>6}")
    for r in results:
        cl="无上限" if r["cap"]>=99999 else f"≤{r['cap']}元"
        print(f"  {cl:<10}{r['ann']:>7.1f}%{r['dd']:>7.1f}%{r['sharpe']:>7.2f}{r['wr']:>6.1f}%{r['n']:>6}")

if __name__=="__main__":
    main()
