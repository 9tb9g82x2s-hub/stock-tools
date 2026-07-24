#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
排查 S009 为何6月踏空光通信：
1. 6月光通信龙头实际涨幅（6/2→6/30）
2. 这些票在6/1调仓时模型给了多少分，排名第几
3. 是否被 blacklist / .BJ / 缺失因子 排除
"""
import sqlite3, time
import pandas as pd, numpy as np, lightgbm as lgb

DB = '/Users/ziruzhu/stock-data/stock_all.db'
PANEL = '/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股/features_panel.pkl'
FEATURE_COLS = ["mom_5","mom_10","mom_20","mom_60","mom_120","turnover_rate","turnover_rate_f","volume_ratio","vol_chg_20","bias_5","bias_10","bias_20","bias_60","macd_dif","macd_dea","macd","kdj_k","kdj_d","kdj_j","rsi_6","rsi_12","rsi_24","cci","boll_pct","boll_width","pe","pe_ttm","pb","ps","ps_ttm","dv_ttm","net_mf_ratio","lg_buy_ratio"]

def log(m): print("[%s] %s" % (time.strftime('%H:%M:%S'), m), flush=True)

con = sqlite3.connect(DB)
st_list = set(pd.read_sql("SELECT ts_code FROM blacklist_st", con)["ts_code"])
loss_list = set(pd.read_sql("SELECT ts_code FROM blacklist_loss", con)["ts_code"])
sinfo = pd.read_sql("SELECT ts_code,name,industry FROM stock_list", con)

# 光通信/通信设备板块龙头（按6月涨幅找）
comm = sinfo[sinfo["industry"].isin(["通信设备"])]["ts_code"].tolist()
log("通信设备板块股票数: %d" % len(comm))

# 6月涨幅 (6/2开盘 -> 6/30收盘)
codes = ",".join(["'%s'" % c for c in comm])
q = "SELECT ts_code,trade_date,open,close FROM daily WHERE ts_code IN (%s) AND trade_date IN ('20260602','20260630')" % codes
df = pd.read_sql(q, con)
df["open"]=df["open"].astype(float); df["close"]=df["close"].astype(float)
op = df[df["trade_date"]=="20260602"].set_index("ts_code")["open"]
cl = df[df["trade_date"]=="20260630"].set_index("ts_code")["close"]
sname = sinfo.set_index("ts_code")["name"]
rows=[]
for c in comm:
    if c in op.index and c in cl.index and op[c]>0:
        rows.append({"code":c,"name":sname.get(c,""),"ret":round((cl[c]/op[c]-1)*100,2)})
comm_ret = pd.DataFrame(rows).sort_values("ret",ascending=False)
log("通信设备6月涨幅TOP15:")
for _,r in comm_ret.head(15).iterrows():
    print("  %-10s %+.1f%%" % (r["name"], r["ret"]))

# ---- 重建6/1调仓的打分，看光通信龙头排名 ----
log("重建6/1调仓打分...")
panel = pd.read_pickle(PANEL)
bl = st_list | loss_list
panel_f = panel[~panel["ts_code"].isin(bl)]
panel_f = panel_f[~panel_f["ts_code"].str.endswith(".BJ")].reset_index(drop=True)
panel_f = panel_f.dropna(subset=FEATURE_COLS, how="all")

ts = "20250601"
tr = panel_f[(panel_f["trade_date"]>=ts)&(panel_f["trade_date"]<"20260601")].dropna(subset=FEATURE_COLS+["label"])
sc = panel_f[panel_f["trade_date"]=="20260601"].dropna(subset=FEATURE_COLS, how="all").copy()
m = lgb.LGBMClassifier(boosting_type="gbdt",num_leaves=31,learning_rate=0.05,n_estimators=200,subsample=0.8,colsample_bytree=0.8,random_state=42,verbose=-1)
m.fit(tr[FEATURE_COLS], tr["label"])
sc["score"] = m.predict_proba(sc[FEATURE_COLS])[:,1]
sc = sc.sort_values("score",ascending=False).reset_index(drop=True)
sc["rank"] = sc.index + 1
total = len(sc)
log("6/1评分股票总数: %d" % total)

# 光通信龙头前10涨幅股的打分情况
print("\n=== 6月涨幅TOP10通信股 在6/1调仓时的模型打分/排名/是否被排除 ===")
print("  %-10s%8s%8s%10s%8s  %s" % ("股票","6月涨幅","模型分","排名","进Top20","状态"))
scmap = sc.set_index("ts_code")
for _,r in comm_ret.head(10).iterrows():
    c=r["code"]; nm=r["name"]; ret=r["ret"]
    if c in st_list: status="被排除(ST)"
    elif c in loss_list: status="被排除(亏损股)"
    elif c.endswith(".BJ"): status="被排除(北交所)"
    elif c not in panel["ts_code"].values: status="面板无此股"
    elif c not in scmap.index:
        # 在面板但6/1无有效因子
        in_raw = c in panel[panel["trade_date"]=="20260601"]["ts_code"].values
        status="6/1因子缺失" if in_raw else "6/1无数据"
    else:
        rank=int(scmap.loc[c,"rank"]); scr=round(float(scmap.loc[c,"score"]),4)
        top="是" if rank<=20 else "否"
        print("  %-10s%+7.1f%%%8.4f%8d/%d%8s  评分正常" % (nm,ret,scr,rank,total,top))
        continue
    print("  %-10s%+7.1f%%%8s%10s%8s  %s" % (nm,ret,"—","—","—",status))

# 亏损股名单里有多少通信股（光模块公司常年高研发可能亏损）
comm_in_loss = [c for c in comm if c in loss_list]
comm_in_st = [c for c in comm if c in st_list]
log("通信设备板块中: 被列亏损股 %d 只, 被列ST %d 只" % (len(comm_in_loss), len(comm_in_st)))
# 具体是哪些高涨幅的被亏损排除
print("\n=== 6月大涨但被'亏损股'规则排除的通信股 ===")
for _,r in comm_ret.head(20).iterrows():
    if r["code"] in loss_list:
        print("  %-10s %+.1f%%  (归母净利为负->被排除)" % (r["name"], r["ret"]))

con.close()
