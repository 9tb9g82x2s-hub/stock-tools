#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S009 point-in-time(时点还原) 回测 —— 修正幸存者偏差
核心改动：blacklist_loss 不再用静态终点名单，而是每个调仓日用
"当时已公告(ann_date < 调仓日)的最新一期财报"判断归母净利是否为负。

对比原版(static blacklist) 看超额收益缩水多少 —— 验证幸存者偏差幅度。

其余设定完全复用原版 train_backtest.py：
- 32特征 LightGBM, 过去12个月滚动训练, Top20等权, 月度调仓
- T+1开盘价成交, 计入佣金+印花税
- 剔除北交所(.BJ)
- 停牌股(无价格)自动跳过
- ST: 库无历史名称表, 沿用当前ST名单近似(多数ST同时亏损, 已被时点亏损过滤覆盖)
"""
import json, sqlite3, time
import numpy as np, pandas as pd
import lightgbm as lgb

BASE = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股"
PANEL = f"{BASE}/features_panel.pkl"
DB = "/Users/ziruzhu/stock-data/stock_all.db"

BACKTEST_START = "20170101"
TRAIN_MONTHS = 12
TOP_N = 20
BUY_COMMISSION = 0.00025
SELL_COMMISSION = 0.00025
STAMP_TAX = 0.0005

FEATURE_COLS = ["mom_5","mom_10","mom_20","mom_60","mom_120","turnover_rate","turnover_rate_f","volume_ratio","vol_chg_20","bias_5","bias_10","bias_20","bias_60","macd_dif","macd_dea","macd","kdj_k","kdj_d","kdj_j","rsi_6","rsi_12","rsi_24","cci","boll_pct","boll_width","pe","pe_ttm","pb","ps","ps_ttm","dv_ttm","net_mf_ratio","lg_buy_ratio"]

def log(m): print("[%s] %s" % (time.strftime('%H:%M:%S'), m), flush=True)

def load_pit_income():
    """加载全市场归母净利润历史(ann_date, end_date, n_income_attr_p)"""
    con = sqlite3.connect(DB)
    inc = pd.read_sql("SELECT ts_code, ann_date, end_date, n_income_attr_p FROM income WHERE ann_date IS NOT NULL AND n_income_attr_p IS NOT NULL", con)
    con.close()
    inc["n_income_attr_p"] = pd.to_numeric(inc["n_income_attr_p"], errors="coerce")
    inc = inc.dropna(subset=["n_income_attr_p"])
    inc = inc[inc["ann_date"].str.len()==8]
    return inc

def get_pit_loss_set(inc, rebal_date):
    """时点亏损判断: 对每只股票, 取 ann_date < rebal_date 的最新一期财报,
       若该期归母净利<0 则视为亏损股(当时可见信息)。
       用最新'年报'口径判断更贴近实际ST规则(连续亏损看年报),
       但为简化且更严格, 这里用最近一期已公告财报(季报/年报均可)的归母净利。
    """
    visible = inc[inc["ann_date"] < rebal_date]
    if visible.empty:
        return set()
    # 每只股票取 ann_date 最新的一条(已公告的最近财报)
    latest = visible.sort_values("ann_date").groupby("ts_code").tail(1)
    loss = set(latest[latest["n_income_attr_p"] < 0]["ts_code"])
    return loss

def get_month_start_dates(trade_dates):
    s = pd.Series(pd.to_datetime(trade_dates, format="%Y%m%d"))
    df = pd.DataFrame({"date": s, "trade_date": trade_dates})
    df["ym"] = df["date"].dt.to_period("M")
    return df.groupby("ym").first()["trade_date"].tolist()

def main():
    t0 = time.time()
    log("读面板...")
    panel = pd.read_pickle(PANEL)
    log("面板 %d 行" % len(panel))

    # ST 名单沿用当前(库无历史), 亏损改时点判断
    con = sqlite3.connect(DB)
    st_set = set(pd.read_sql("SELECT ts_code FROM blacklist_st", con)["ts_code"])
    con.close()
    log("当前ST名单 %d 只(近似, 库无历史ST)" % len(st_set))

    log("加载时点财报(income)...")
    inc = load_pit_income()
    log("财报记录 %d 条, 覆盖 %d 只股票" % (len(inc), inc["ts_code"].nunique()))

    # 只剔除北交所 + 特征全空(ST和亏损放到每期动态处理)
    panel = panel[~panel["ts_code"].str.endswith(".BJ")].reset_index(drop=True)
    panel = panel.dropna(subset=FEATURE_COLS, how="all")

    all_dates = sorted(panel["trade_date"].unique())
    month_dates = get_month_start_dates(all_dates)
    rebalance_dates = [d for d in month_dates if d >= BACKTEST_START]
    log("调仓期数 %d" % len(rebalance_dates))

    panel_by_date = {d: sub for d, sub in panel.groupby("trade_date")}
    open_lookup = panel.set_index(["ts_code","trade_date"])["open_qfq"].sort_index()
    next_trade_date = {d: all_dates[i+1] for i,d in enumerate(all_dates) if i+1<len(all_dates)}

    nav = 1.0
    nav_curve = []
    trades = []
    prev_holdings = set()

    for i, rd in enumerate(rebalance_dates):
        # ==== 时点黑名单(核心修正) ====
        pit_loss = get_pit_loss_set(inc, rd)
        blacklist = st_set | pit_loss

        rd_dt = pd.to_datetime(rd, format="%Y%m%d")
        train_start = (rd_dt - pd.DateOffset(months=TRAIN_MONTHS)).strftime("%Y%m%d")

        train_mask = (panel["trade_date"] >= train_start) & (panel["trade_date"] < rd)
        train_df = panel.loc[train_mask].dropna(subset=FEATURE_COLS+["label"])
        # 训练集也剔除时点黑名单(训练标签也不该学已知亏损股)
        train_df = train_df[~train_df["ts_code"].isin(blacklist)]
        if len(train_df) < 5000:
            continue

        score_df = panel_by_date.get(rd)
        if score_df is None or len(score_df)==0:
            continue
        score_df = score_df.dropna(subset=FEATURE_COLS, how="all").copy()
        # 评分池剔除时点黑名单
        score_df = score_df[~score_df["ts_code"].isin(blacklist)]
        if len(score_df)==0:
            continue

        m = lgb.LGBMClassifier(boosting_type="gbdt",num_leaves=31,learning_rate=0.05,n_estimators=200,subsample=0.8,colsample_bytree=0.8,random_state=42,verbose=-1)
        m.fit(train_df[FEATURE_COLS], train_df["label"])
        score_df["score"] = m.predict_proba(score_df[FEATURE_COLS])[:,1]
        top20 = score_df.sort_values("score",ascending=False).head(TOP_N)["ts_code"].tolist()

        next_rd = rebalance_dates[i+1] if i+1<len(rebalance_dates) else None
        if next_rd is None: break
        buy_date = next_trade_date.get(rd)
        sell_date = next_trade_date.get(next_rd)
        if buy_date is None or sell_date is None: continue

        rets=[]; valid=[]
        for code in top20:
            try:
                p0=open_lookup.loc[(code,buy_date)]; p1=open_lookup.loc[(code,sell_date)]
                if pd.isna(p0) or pd.isna(p1) or p0<=0: continue
                rets.append(float(p1)/float(p0)-1); valid.append(code)
            except KeyError:
                continue
        if not rets: continue

        gross=float(np.mean(rets))
        curr=set(valid)
        bought=curr-prev_holdings; sold=prev_holdings-curr
        nc=len(curr) or 1; npv=len(prev_holdings) or 1
        bt=len(bought)/nc; st=len(sold)/npv if npv>0 else 0
        cost=bt*BUY_COMMISSION+st*(SELL_COMMISSION+STAMP_TAX)
        pr=gross-cost
        nav*=(1+pr)
        nav_curve.append({"date":sell_date,"nav":round(nav,6)})
        trades.append({"rebalance_date":rd,"buy_date":buy_date,"sell_date":sell_date,
                       "n_blacklist":len(blacklist),"n_pit_loss":len(pit_loss),
                       "gross_return":round(gross,6),"period_return":round(pr,6),
                       "win_count":int(sum(1 for r in rets if r>0)),"n_holdings":len(valid)})
        prev_holdings=curr
        if (i+1)%12==0:
            log("已完成 %d/%d 期, 当前净值 %.4f (本期时点亏损股 %d 只)" % (i+1,len(rebalance_dates),nav,len(pit_loss)))

    # ==== 指标 ====
    prs=np.array([t["period_return"] for t in trades])
    n=len(prs); total_ret=nav-1
    n_years=n/12.0 if n>0 else 1
    annual=(nav**(1/n_years)-1) if n_years>0 and nav>0 else 0
    navs=np.array([1.0]+[c["nav"] for c in nav_curve])
    dd=(navs/np.maximum.accumulate(navs)-1); mdd=float(dd.min())
    win=float(np.mean(prs>0)) if n>0 else 0
    sharpe=float(prs.mean()/prs.std()*np.sqrt(12)) if n>1 and prs.std()>0 else 0

    # 按年
    tdf=pd.DataFrame(trades)
    tdf["year"]=tdf["rebalance_date"].str[:4]
    yearly=[]
    for yr,g in tdf.groupby("year"):
        yr_ret=float(np.prod(1+g["period_return"].values)-1)
        yearly.append({"year":yr,"periods":len(g),"return_pct":round(yr_ret*100,2),
                       "avg_pit_loss":int(g["n_pit_loss"].mean()),"avg_win":round(float(g["win_count"].mean()),1)})

    metrics={"total_return":round(float(total_ret),4),"annual_return":round(float(annual),4),
             "win_rate":round(win,4),"max_drawdown":round(mdd,4),"sharpe_ratio":round(sharpe,4),"total_trades":n}
    result={"strategy":"S009-PIT(时点还原黑名单)","metrics":metrics,"yearly":yearly,
            "nav_curve":nav_curve,"trades":trades}
    json.dump(result, open(f"{BASE}/pit_backtest_result.json","w"), ensure_ascii=False, indent=2)

    log("="*60)
    log("时点还原版结果:")
    log("  总收益 %.1f%%  年化 %.2f%%  夏普 %.2f  最大回撤 %.1f%%  胜率 %.1f%%" % (total_ret*100,annual*100,sharpe,mdd*100,win*100))
    print("\n年度收益(时点还原):")
    print("%-6s%8s%12s%12s%8s" % ("年份","期数","收益","时点亏损股","均胜"))
    for y in yearly:
        print("%-6s%8d%11.2f%%%12d%8.1f" % (y["year"],y["periods"],y["return_pct"],y["avg_pit_loss"],y["avg_win"]))
    log("耗时 %.1fs" % (time.time()-t0))

if __name__=="__main__":
    main()
