#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S009 v1.4 — 加大盘择时开关
在 v1.3(时点还原PIT + T+1开盘 + 成本) 基础上加一层择时:
  每个调仓日, 查 timing_signal.json:
    - bull(市场中位价 >= MA20): 正常满仓 Top20
    - 空头(市场中位价 < MA20): 本期空仓, 收益=0(持有现金), 不产生交易成本
择时信号用调仓日当天数据, 无未来函数。

对比 v1.3(PIT无择时) vs v1.4(PIT+择时), 看股灾/熊市回撤能压多少。
"""
import json, sqlite3, time
import numpy as np, pandas as pd
import lightgbm as lgb

BASE = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股"
PANEL = f"{BASE}/features_panel.pkl"
DB = "/Users/ziruzhu/stock-data/stock_all.db"

BACKTEST_START = "20170101"
TRAIN_MONTHS = 12; TOP_N = 20
BUY_COMMISSION = 0.00025; SELL_COMMISSION = 0.00025; STAMP_TAX = 0.0005

FEATURE_COLS = ["mom_5","mom_10","mom_20","mom_60","mom_120","turnover_rate","turnover_rate_f","volume_ratio","vol_chg_20","bias_5","bias_10","bias_20","bias_60","macd_dif","macd_dea","macd","kdj_k","kdj_d","kdj_j","rsi_6","rsi_12","rsi_24","cci","boll_pct","boll_width","pe","pe_ttm","pb","ps","ps_ttm","dv_ttm","net_mf_ratio","lg_buy_ratio"]

def log(m): print("[%s] %s" % (time.strftime('%H:%M:%S'), m), flush=True)

def load_pit_income():
    con = sqlite3.connect(DB)
    inc = pd.read_sql("SELECT ts_code,ann_date,end_date,n_income_attr_p FROM income WHERE ann_date IS NOT NULL AND n_income_attr_p IS NOT NULL", con)
    con.close()
    inc["n_income_attr_p"] = pd.to_numeric(inc["n_income_attr_p"], errors="coerce")
    inc = inc.dropna(subset=["n_income_attr_p"])
    return inc[inc["ann_date"].str.len()==8]

def pit_loss(inc, rd):
    v = inc[inc["ann_date"] < rd]
    if v.empty: return set()
    latest = v.sort_values("ann_date").groupby("ts_code").tail(1)
    return set(latest[latest["n_income_attr_p"] < 0]["ts_code"])

def month_starts(dates):
    s = pd.Series(pd.to_datetime(dates, format="%Y%m%d"))
    df = pd.DataFrame({"date": s, "trade_date": dates}); df["ym"] = df["date"].dt.to_period("M")
    return df.groupby("ym").first()["trade_date"].tolist()

def run_backtest(use_timing):
    panel = pd.read_pickle(PANEL)
    con = sqlite3.connect(DB)
    st_set = set(pd.read_sql("SELECT ts_code FROM blacklist_st", con)["ts_code"]); con.close()
    inc = load_pit_income()
    timing = json.load(open(f"{BASE}/timing_signal.json")) if use_timing else {}

    panel = panel[~panel["ts_code"].str.endswith(".BJ")].reset_index(drop=True)
    panel = panel.dropna(subset=FEATURE_COLS, how="all")
    all_dates = sorted(panel["trade_date"].unique())
    rebal = [d for d in month_starts(all_dates) if d >= BACKTEST_START]
    panel_by_date = {d: s for d, s in panel.groupby("trade_date")}
    op = panel.set_index(["ts_code","trade_date"])["open_qfq"].sort_index()
    next_td = {d: all_dates[i+1] for i,d in enumerate(all_dates) if i+1<len(all_dates)}

    nav=1.0; navc=[]; trades=[]; prev=set(); n_cash=0
    for i, rd in enumerate(rebal):
        next_rd = rebal[i+1] if i+1<len(rebal) else None
        if next_rd is None: break
        bd = next_td.get(rd); sd = next_td.get(next_rd)
        if bd is None or sd is None: continue

        # 择时: 调仓日空头 -> 空仓
        is_bull = True
        if use_timing:
            sig = timing.get(rd)
            is_bull = sig["bull"] if sig else True
        if use_timing and not is_bull:
            # 空仓: 收益0, 清仓(若上期有持仓, 付卖出成本)
            sell_cost = (len(prev)/max(len(prev),1))*(SELL_COMMISSION+STAMP_TAX) if prev else 0
            pr = -sell_cost
            nav *= (1+pr)
            navc.append({"date": sd, "nav": round(nav,6), "cash": True})
            trades.append({"rebalance_date": rd, "cash": True, "period_return": round(pr,6), "win_count": 0, "n_holdings": 0})
            prev = set(); n_cash += 1
            continue

        # 正常选股
        blk = st_set | pit_loss(inc, rd)
        ts = (pd.to_datetime(rd, format="%Y%m%d") - pd.DateOffset(months=TRAIN_MONTHS)).strftime("%Y%m%d")
        tr = panel[(panel["trade_date"]>=ts)&(panel["trade_date"]<rd)].dropna(subset=FEATURE_COLS+["label"])
        tr = tr[~tr["ts_code"].isin(blk)]
        if len(tr)<5000: continue
        sc = panel_by_date.get(rd)
        if sc is None: continue
        sc = sc.dropna(subset=FEATURE_COLS, how="all").copy()
        sc = sc[~sc["ts_code"].isin(blk)]
        if len(sc)==0: continue
        m = lgb.LGBMClassifier(boosting_type="gbdt",num_leaves=31,learning_rate=0.05,n_estimators=200,subsample=0.8,colsample_bytree=0.8,random_state=42,verbose=-1)
        m.fit(tr[FEATURE_COLS], tr["label"])
        sc["score"] = m.predict_proba(sc[FEATURE_COLS])[:,1]
        top20 = sc.sort_values("score",ascending=False).head(TOP_N)["ts_code"].tolist()

        rets=[]; valid=[]
        for c in top20:
            try:
                p0=op.loc[(c,bd)]; p1=op.loc[(c,sd)]
                if pd.isna(p0) or pd.isna(p1) or p0<=0: continue
                rets.append(float(p1)/float(p0)-1); valid.append(c)
            except KeyError: continue
        if not rets: continue
        gross=float(np.mean(rets)); curr=set(valid)
        bt=len(curr-prev)/(len(curr) or 1); st=len(prev-curr)/(len(prev) or 1) if prev else 0
        cost=bt*BUY_COMMISSION+st*(SELL_COMMISSION+STAMP_TAX)
        pr=gross-cost; nav*=(1+pr)
        navc.append({"date": sd, "nav": round(nav,6), "cash": False})
        trades.append({"rebalance_date": rd, "cash": False, "period_return": round(pr,6),
                       "win_count": int(sum(1 for r in rets if r>0)), "n_holdings": len(valid)})
        prev=curr

    prs=np.array([t["period_return"] for t in trades]); n=len(prs)
    ny=n/12.0 if n>0 else 1
    annual=(nav**(1/ny)-1) if ny>0 and nav>0 else 0
    navs=np.array([1.0]+[c["nav"] for c in navc])
    dd=(navs/np.maximum.accumulate(navs)-1); mdd=float(dd.min())
    win=float(np.mean(prs>0)) if n>0 else 0
    sharpe=float(prs.mean()/prs.std()*np.sqrt(12)) if n>1 and prs.std()>0 else 0
    # 按年
    tdf=pd.DataFrame(trades); tdf["year"]=tdf["rebalance_date"].str[:4]
    yearly={}
    for yr,g in tdf.groupby("year"):
        yearly[yr]={"return_pct":round((np.prod(1+g["period_return"].values)-1)*100,2),
                    "cash_periods":int(g["cash"].sum()) if "cash" in g else 0}
    return {"nav":round(nav,4),"total_return_pct":round((nav-1)*100,1),"annual_pct":round(annual*100,2),
            "mdd_pct":round(mdd*100,2),"sharpe":round(sharpe,2),"win_pct":round(win*100,1),
            "n_periods":n,"n_cash":n_cash,"yearly":yearly,"navc":navc}

log("跑 v1.3 (PIT无择时, 基准)...")
v13 = run_backtest(use_timing=False)
log("v1.3: 累计%s%% 年化%s%% 回撤%s%% 夏普%s" % (v13["total_return_pct"],v13["annual_pct"],v13["mdd_pct"],v13["sharpe"]))

log("跑 v1.4 (PIT+择时)...")
v14 = run_backtest(use_timing=True)
log("v1.4: 累计%s%% 年化%s%% 回撤%s%% 夏普%s (空仓%d期)" % (v14["total_return_pct"],v14["annual_pct"],v14["mdd_pct"],v14["sharpe"],v14["n_cash"]))

json.dump({"v13_pit":{k:v for k,v in v13.items() if k!="navc"},
           "v14_timing":{k:v for k,v in v14.items() if k!="navc"},
           "v13_navc":v13["navc"],"v14_navc":v14["navc"]},
          open(f"{BASE}/v14_timing_result.json","w"), ensure_ascii=False, indent=2)

print("\n"+"="*70)
print("%-8s%14s%14s%14s%10s%8s" % ("版本","累计收益","年化","最大回撤","夏普","空仓期"))
print("-"*70)
print("%-8s%13.0f%%%13.2f%%%13.2f%%%10.2f%8s" % ("v1.3无择时",v13["total_return_pct"],v13["annual_pct"],v13["mdd_pct"],v13["sharpe"],"-"))
print("%-8s%13.0f%%%13.2f%%%13.2f%%%10.2f%8d" % ("v1.4择时",v14["total_return_pct"],v14["annual_pct"],v14["mdd_pct"],v14["sharpe"],v14["n_cash"]))
print("="*70)
print("\n逐年对比(收益% / 空仓期数):")
print("%-6s%16s%20s" % ("年份","v1.3无择时","v1.4择时(空仓数)"))
for yr in sorted(v13["yearly"].keys()):
    a=v13["yearly"][yr]["return_pct"]; b=v14["yearly"][yr]["return_pct"]; c=v14["yearly"][yr]["cash_periods"]
    print("%-6s%15.1f%%%15.1f%% (空%d)" % (yr,a,b,c))
