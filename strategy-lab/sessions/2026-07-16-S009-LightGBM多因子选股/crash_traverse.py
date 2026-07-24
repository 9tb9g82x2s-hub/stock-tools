#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
精准版：S009 在 2026-06-01 调仓选出的Top20，持有穿越7月股灾到7/17
月度调仓：6月1日调仓后，下一次调仓要到8月，所以6月持仓完整覆盖整个7月股灾。
计算：
  A) 6月买入价 → 7/17收盘价 的完整区间收益（穿越股灾）
  B) 6月买入价 → 7/16收盘价 的收益（股灾前一天，即6月这波最高点附近）
  C) 7/16收盘 → 7/17收盘 单日股灾收益 vs 全市场中位数
"""
import json, sqlite3, time
import numpy as np, pandas as pd
import lightgbm as lgb

BASE_DIR = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股"
PANEL_PATH = f"{BASE_DIR}/features_panel.pkl"
DB_PATH = "/Users/ziruzhu/stock-data/stock_all.db"
TRAIN_MONTHS = 12; TOP_N = 20
REBAL = "20260601"
FEATURE_COLS = ["mom_5","mom_10","mom_20","mom_60","mom_120","turnover_rate","turnover_rate_f","volume_ratio","vol_chg_20","bias_5","bias_10","bias_20","bias_60","macd_dif","macd_dea","macd","kdj_k","kdj_d","kdj_j","rsi_6","rsi_12","rsi_24","cci","boll_pct","boll_width","pe","pe_ttm","pb","ps","ps_ttm","dv_ttm","net_mf_ratio","lg_buy_ratio"]

def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

con = sqlite3.connect(DB_PATH)
bl = set(pd.read_sql("SELECT ts_code FROM blacklist_st", con)["ts_code"]) | set(pd.read_sql("SELECT ts_code FROM blacklist_loss", con)["ts_code"])
sinfo = pd.read_sql("SELECT ts_code,name,industry FROM stock_list", con).set_index("ts_code")
con.close()

log("读面板...")
panel = pd.read_pickle(PANEL_PATH)
panel = panel[~panel["ts_code"].isin(bl)]
panel = panel[~panel["ts_code"].str.endswith(".BJ")].reset_index(drop=True)
panel = panel.dropna(subset=FEATURE_COLS, how="all")
all_dates = sorted(panel["trade_date"].unique())
next_td = {d: all_dates[i+1] for i,d in enumerate(all_dates) if i+1 < len(all_dates)}
open_lk = panel.set_index(["ts_code","trade_date"])["open_qfq"].sort_index()
close_lk = panel.set_index(["ts_code","trade_date"])["close_qfq"].sort_index()

# 训练：过去12个月，标签已知（< REBAL）
ts = (pd.to_datetime(REBAL, format="%Y%m%d") - pd.DateOffset(months=TRAIN_MONTHS)).strftime("%Y%m%d")
tr = panel[(panel["trade_date"]>=ts)&(panel["trade_date"]<REBAL)].dropna(subset=FEATURE_COLS+["label"])
sc = panel[panel["trade_date"]==REBAL].dropna(subset=FEATURE_COLS, how="all").copy()
log(f"训练样本{len(tr):,}, 评分{len(sc)}")

m = lgb.LGBMClassifier(boosting_type="gbdt",num_leaves=31,learning_rate=0.05,n_estimators=200,subsample=0.8,colsample_bytree=0.8,random_state=42,verbose=-1)
m.fit(tr[FEATURE_COLS], tr["label"])
sc["score"] = m.predict_proba(sc[FEATURE_COLS])[:,1]
top20 = sc.sort_values("score",ascending=False).head(TOP_N)["ts_code"].tolist()

buy_date = next_td.get(REBAL)   # T+1开盘买入
log(f"调仓日{REBAL}, 买入日{buy_date}")

rows = []
for code in top20:
    nm = sinfo.loc[code]["name"] if code in sinfo.index else ""
    ind = sinfo.loc[code]["industry"] if code in sinfo.index else ""
    try:
        p_buy = float(open_lk.loc[(code, buy_date)])
        p_716 = float(close_lk.loc[(code, "20260716")])
        p_717 = float(close_lk.loc[(code, "20260717")])
        r_full = p_717/p_buy - 1       # 买入→7/17（穿越股灾）
        r_pre  = p_716/p_buy - 1       # 买入→7/16（股灾前）
        r_crash= p_717/p_716 - 1       # 7/17单日股灾
        rows.append({"code":code,"name":nm,"ind":ind,"p_buy":round(p_buy,2),"p_716":round(p_716,2),"p_717":round(p_717,2),
                     "r_full":round(r_full*100,2),"r_pre":round(r_pre*100,2),"r_crash":round(r_crash*100,2)})
    except KeyError:
        continue

df = pd.DataFrame(rows)
# 全市场7/17单日中位数
m16 = panel[panel["trade_date"]=="20260716"][["ts_code","close_qfq"]]
m17 = panel[panel["trade_date"]=="20260717"][["ts_code","close_qfq"]]
mg = m16.merge(m17,on="ts_code",suffixes=("_16","_17"))
mg = mg[(mg["close_qfq_16"]>0)&(mg["close_qfq_17"]>0)]
mg["r"] = mg["close_qfq_17"]/mg["close_qfq_16"]-1
mkt_crash = float(mg["r"].median())*100

# 组合层面（等权）
port_full = df["r_full"].mean()
port_pre  = df["r_pre"].mean()
port_crash= df["r_crash"].mean()

out = {
  "rebalance": REBAL, "buy_date": buy_date, "hold_until": "20260717",
  "portfolio": {
    "return_buy_to_716_pct": round(port_pre,2),
    "return_buy_to_717_pct": round(port_full,2),
    "crash_day_717_pct": round(port_crash,2),
    "market_median_717_pct": round(mkt_crash,2),
    "excess_vs_market_717": round(port_crash-mkt_crash,2),
    "win_full": int((df["r_full"]>0).sum()), "total": len(df),
  },
  "holdings": df.sort_values("r_full",ascending=False).to_dict("records"),
}
json.dump(out, open(f"{BASE_DIR}/crash_traverse_result.json","w"), ensure_ascii=False, indent=2)

print("\n===== S009 6月持仓穿越7月股灾 =====")
print(f"调仓日 {REBAL} 选股，{buy_date} 开盘买入，持有到 7/17")
print(f"组合 买入→7/16(股灾前): {port_pre:+.2f}%")
print(f"组合 买入→7/17(穿越股灾): {port_full:+.2f}%")
print(f"7/17单日: 组合 {port_crash:+.2f}%  vs  全市场中位数 {mkt_crash:+.2f}%  (超额{port_crash-mkt_crash:+.2f}%)")
print(f"穿越股灾后 盈利股数: {int((df['r_full']>0).sum())}/{len(df)}")
print("\n=== 持仓明细(按穿越股灾总收益排序) ===")
print(f"{'股票':9s}{'行业':9s}{'买入':>8s}{'7/16':>8s}{'7/17':>8s}{'→716':>8s}{'→717':>8s}{'灾日':>8s}")
for r in out["holdings"]:
    print(f"{r['name']:9s}{r['ind']:9s}{r['p_buy']:>8.2f}{r['p_716']:>8.2f}{r['p_717']:>8.2f}{r['r_pre']:>+8.1f}{r['r_full']:>+8.1f}{r['r_crash']:>+8.1f}")
log("done")
