#!/usr/bin/env python3
# S019高频10日换仓 单cap回测（pf_single的S019版本，10天label）
import json, sqlite3, time, os, gc, sys
import numpy as np, pandas as pd
import lightgbm as lgb

SOURCE = "s019"; CAP = int(sys.argv[1]) if len(sys.argv) > 1 else 99999
PANEL_PATH="/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S013-喜神池LightGBM选股/features_panel.pkl"
USE_XISHEN=True
OUT_DIR="/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S014-Top精选集中策略"
DB_PATH="/Users/ziruzhu/stock-data/stock_all.db"
XISHEN_PATH="/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S013-喜神池LightGBM选股/xishen_plus_pool.csv"
OUT_FILE=f"{OUT_DIR}/pricefilter_{SOURCE}_result.json"
HORIZON=10; BACKTEST_START="20170101"; TRAIN_MONTHS=12; TOP_N=20; STOP_LOSS=-0.12; BC,SC=0.00025,0.00125
FEATURE_COLS=["mom_5","mom_10","mom_20","mom_60","mom_120","turnover_rate","turnover_rate_f","volume_ratio","vol_chg_20",
    "bias_5","bias_10","bias_20","bias_60","macd_dif","macd_dea","macd","kdj_k","kdj_d","kdj_j",
    "rsi_6","rsi_12","rsi_24","cci","boll_pct","boll_width","pe","pe_ttm","pb","ps","ps_ttm","dv_ttm","net_mf_ratio","lg_buy_ratio"]

def load_bl():
    con=sqlite3.connect(DB_PATH)
    st=pd.read_sql("SELECT ts_code FROM blacklist_st",con)["ts_code"].tolist()
    loss=pd.read_sql("SELECT ts_code FROM blacklist_loss",con)["ts_code"].tolist()
    con.close(); return set(st)|set(loss)
def get_10d_reb(td):
    # S019每10个交易日调仓
    return [td[i] for i in range(0, len(td), 10) if td[i] >= BACKTEST_START]

t0=time.time()
xishen_set=set(pd.read_csv(XISHEN_PATH)["ts_code"])
need_cols=["ts_code","trade_date","close_qfq","open_qfq","label"]+FEATURE_COLS
panel=pd.read_pickle(PANEL_PATH)
panel=panel[[c for c in need_cols if c in panel.columns]].copy(); gc.collect()
panel=panel.sort_values(["ts_code","trade_date"]).reset_index(drop=True)
panel["fwd"]=panel.groupby("ts_code")["close_qfq"].transform(lambda s:s.shift(-HORIZON)/s-1)
med=panel.groupby("trade_date")["fwd"].transform("median")
panel["label"]=(panel["fwd"]>med).astype("Int8"); panel.loc[panel["fwd"].isna(),"label"]=pd.NA
panel.drop(columns=["fwd"],inplace=True); gc.collect()
# Studio有68G内存，不用省内存
bl=load_bl(); panel=panel[~panel["ts_code"].isin(bl)]; panel=panel[~panel["ts_code"].str.endswith(".BJ")]
panel=panel.dropna(subset=FEATURE_COLS,how="all").reset_index(drop=True); gc.collect()
all_td=sorted(panel["trade_date"].unique()); reb=get_10d_reb(all_td)
pbd={d:sub for d,sub in panel.groupby("trade_date")}
open_lk=panel.set_index(["ts_code","trade_date"])["open_qfq"].sort_index()
close_lk=panel.set_index(["ts_code","trade_date"])["close_qfq"].sort_index()
ntd={d:all_td[i+1] for i,d in enumerate(all_td) if i+1<len(all_td)}
csv_path=f"{OUT_DIR}/rebalance_prices.csv"
if os.path.exists(csv_path):
    rpx=pd.read_csv(csv_path); rpx["close"]=pd.to_numeric(rpx["close"],errors="coerce")
    rpx["trade_date"]=rpx["trade_date"].astype(str)  # 修复：统一为字符串
    rpx_idx=rpx.set_index(["trade_date","ts_code"])["close"]
else:
    conn=sqlite3.connect(DB_PATH); rp=",".join(f"'{d}'" for d in reb)
    rpx=pd.read_sql(f"SELECT ts_code,trade_date,close FROM daily WHERE trade_date IN ({rp})",conn); conn.close()
    rpx["close"]=pd.to_numeric(rpx["close"],errors="coerce"); rpx_idx=rpx.set_index(["trade_date","ts_code"])["close"]

trades=[]; nav=1.0; prev=set()
for i,rd in enumerate(reb):
    rd_dt=pd.to_datetime(rd,format="%Y%m%d"); ts=(rd_dt-pd.DateOffset(months=TRAIN_MONTHS)).strftime("%Y%m%d")
    tr=panel.loc[(panel["trade_date"]>=ts)&(panel["trade_date"]<rd)].dropna(subset=FEATURE_COLS+["label"])
    if len(tr)<5000: continue
    sc=pbd.get(rd)
    if sc is None or len(sc)==0: continue
    sc=sc[sc["ts_code"].isin(xishen_set)].dropna(subset=FEATURE_COLS,how="all").copy()
    if CAP<99999:
        try:
            dpx=rpx_idx.loc[rd]; keep=sc["ts_code"].map(dpx); sc=sc[(keep<=CAP)&(keep.notna())]
        except KeyError: pass
    if len(sc)<TOP_N: continue
    m=lgb.LGBMClassifier(boosting_type="gbdt",num_leaves=31,learning_rate=0.05,n_estimators=200,
        subsample=0.8,colsample_bytree=0.8,random_state=42,verbose=-1,n_jobs=2)
    m.fit(tr[FEATURE_COLS],tr["label"]); sc["score"]=m.predict_proba(sc[FEATURE_COLS])[:,1]
    top=sc.sort_values("score",ascending=False).head(TOP_N)["ts_code"].tolist()
    nrd=reb[i+1] if i+1<len(reb) else None
    if nrd is None: break
    bd=ntd.get(rd); sd=ntd.get(nrd)
    if bd is None or sd is None: continue
    rets,vh=[],[]
    for c in top:
        try:
            ep=open_lk.loc[(c,bd)]
            if pd.isna(ep) or ep<=0: continue
            hit,sp,d=False,None,bd
            while d<sd:
                dn=ntd.get(d)
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
    cost=bt*BC+st*(SC+0.0005); nav*=(1+gr-cost)
    trades.append({"rebalance_date":rd,"buy_date":bd,"sell_date":sd,"period_return":round(gr-cost,6),"n":len(vh)})
    prev=cur
pr=pd.Series([t["period_return"] for t in trades]); navv=(1+pr).cumprod().iloc[-1]
fd=pd.to_datetime(trades[0]["buy_date"]); ld=pd.to_datetime(trades[-1]["sell_date"]); ny=(ld-fd).days/365.25
ann=navv**(1/ny)-1; nc=(1+pr).cumprod(); dd=float(((nc-nc.cummax())/nc.cummax()).min())
sh=float(pr.mean()/pr.std()*np.sqrt(24)) if pr.std()>0 else 0  # 高频约24期/年
res={"cap":CAP,"ann":round(ann*100,1),"dd":round(dd*100,1),"sharpe":round(sh,2),
     "wr":round((pr>0).mean()*100,1),"total":round((navv-1)*100,0),"n":len(trades)}
old=[r for r in (json.load(open(OUT_FILE)) if os.path.exists(OUT_FILE) else []) if r["cap"]!=CAP]
old.append(res); old.sort(key=lambda x:-x["cap"])
json.dump(old,open(OUT_FILE,"w"),ensure_ascii=False,indent=2)
cl="无上限" if CAP>=99999 else f"≤{CAP}元"
print(f"{SOURCE} {cl}: 年化{res['ann']}% 回撤{res['dd']}% 夏普{res['sharpe']} 胜率{res['wr']}% 期数{res['n']} 耗时{time.time()-t0:.0f}s")
