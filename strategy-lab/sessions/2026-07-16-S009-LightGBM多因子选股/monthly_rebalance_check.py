#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
按正确的月度调仓口径评估 S009：
- 6月期：6/1调仓 → 6/2开盘买 → 6/30收盘卖，对比6月大盘
- 7月期：7/1调仓(因子用6/26近似) → 7/2开盘买 → 7/17收盘，对比同期大盘
"""
import sqlite3, time, json
import pandas as pd, numpy as np, lightgbm as lgb

DB = '/Users/ziruzhu/stock-data/stock_all.db'
PANEL = '/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股/features_panel.pkl'
OUT = '/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股/monthly_rebalance_result.json'
FEATURE_COLS = ["mom_5","mom_10","mom_20","mom_60","mom_120","turnover_rate","turnover_rate_f","volume_ratio","vol_chg_20","bias_5","bias_10","bias_20","bias_60","macd_dif","macd_dea","macd","kdj_k","kdj_d","kdj_j","rsi_6","rsi_12","rsi_24","cci","boll_pct","boll_width","pe","pe_ttm","pb","ps","ps_ttm","dv_ttm","net_mf_ratio","lg_buy_ratio"]

def log(m): print("[%s] %s" % (time.strftime('%H:%M:%S'), m), flush=True)

con = sqlite3.connect(DB)
bl = set(pd.read_sql("SELECT ts_code FROM blacklist_st", con)["ts_code"]) | set(pd.read_sql("SELECT ts_code FROM blacklist_loss", con)["ts_code"])
sinfo = pd.read_sql("SELECT ts_code,name,industry FROM stock_list", con).set_index("ts_code")

log("read panel...")
panel = pd.read_pickle(PANEL)
panel = panel[~panel["ts_code"].isin(bl)]
panel = panel[~panel["ts_code"].str.endswith(".BJ")].reset_index(drop=True)
panel = panel.dropna(subset=FEATURE_COLS, how="all")

def get_top20(rebal_date, label):
    ts = (pd.to_datetime(rebal_date, format="%Y%m%d") - pd.DateOffset(months=12)).strftime("%Y%m%d")
    tr = panel[(panel["trade_date"] >= ts) & (panel["trade_date"] < rebal_date)].dropna(subset=FEATURE_COLS + ["label"])
    sc = panel[panel["trade_date"] == rebal_date].dropna(subset=FEATURE_COLS, how="all").copy()
    log("  [%s] train=%d score=%d" % (label, len(tr), len(sc)))
    m = lgb.LGBMClassifier(boosting_type="gbdt", num_leaves=31, learning_rate=0.05,
        n_estimators=200, subsample=0.8, colsample_bytree=0.8, random_state=42, verbose=-1)
    m.fit(tr[FEATURE_COLS], tr["label"])
    sc["score"] = m.predict_proba(sc[FEATURE_COLS])[:, 1]
    return sc.sort_values("score", ascending=False).head(20)["ts_code"].tolist()

def period_stats(top20, buy_date, sell_date, label):
    codes = ",".join(["'%s'" % c for c in top20])
    df = pd.read_sql("SELECT ts_code,trade_date,open,close FROM daily WHERE ts_code IN (%s) AND trade_date IN ('%s','%s')" % (codes, buy_date, sell_date), con)
    df["open"] = df["open"].astype(float); df["close"] = df["close"].astype(float)
    mb = pd.read_sql("SELECT ts_code,open FROM daily WHERE trade_date='%s'" % buy_date, con)
    ms = pd.read_sql("SELECT ts_code,close FROM daily WHERE trade_date='%s'" % sell_date, con)
    mb["open"] = mb["open"].astype(float); ms["close"] = ms["close"].astype(float)
    mg = mb.merge(ms, on="ts_code"); mg = mg[(mg["open"] > 0) & (mg["close"] > 0)]
    mg["r"] = mg["close"] / mg["open"] - 1
    mkt_median = float(mg["r"].median()) * 100
    mkt_mean = float(mg["r"].mean()) * 100
    op = df[df["trade_date"] == buy_date].set_index("ts_code")["open"]
    cl = df[df["trade_date"] == sell_date].set_index("ts_code")["close"]
    rows = []
    for code in top20:
        nm = sinfo.loc[code, "name"] if code in sinfo.index else ""
        ind = sinfo.loc[code, "industry"] if code in sinfo.index else ""
        pb = op.get(code, np.nan); ps = cl.get(code, np.nan)
        if pd.isna(pb) or pb <= 0 or pd.isna(ps): continue
        rows.append({"name": nm, "ind": ind, "buy": round(float(pb),2), "sell": round(float(ps),2), "r_pct": round((ps/pb-1)*100, 2)})
    res = pd.DataFrame(rows)
    return {"label": label, "buy_date": buy_date, "sell_date": sell_date,
            "port": round(float(res["r_pct"].mean()), 2),
            "win": int((res["r_pct"] > 0).sum()), "total": len(res),
            "mkt_median": round(mkt_median, 2), "mkt_mean": round(mkt_mean, 2),
            "excess": round(float(res["r_pct"].mean()) - mkt_median, 2),
            "holdings": res.sort_values("r_pct", ascending=False).to_dict("records")}

log("--- June ---")
jun = period_stats(get_top20("20260601", "Jun"), "20260602", "20260630", "6月期")
log("--- July (approx, factor@6/26) ---")
jul = period_stats(get_top20("20260626", "Jul"), "20260702", "20260717", "7月期")
con.close()

result = {"june": jun, "july": jul,
          "july_note": "面板因子最新到6/26，用作7/1调仓的近似评分日（差3个交易日）"}
json.dump(result, open(OUT, "w"), ensure_ascii=False, indent=2)

def show(s, price_label):
    print("=" * 58)
    print("【%s】买%s开盘 → 卖%s收盘" % (s["label"], s["buy_date"], s["sell_date"]))
    print("  S009组合:   %+.2f%%  (%d/%d 盈利)" % (s["port"], s["win"], s["total"]))
    print("  全市场中位: %+.2f%%   均值: %+.2f%%" % (s["mkt_median"], s["mkt_mean"]))
    print("  超额收益:   %+.2f%%" % s["excess"])
    print("  持仓明细:")
    print("  %-8s%-9s%8s%8s%9s" % ("股票","行业","买入",price_label,"收益"))
    for r in s["holdings"]:
        mark = "+" if r["r_pct"] > 0 else "-"
        print("  %-8s%-9s%8.2f%8.2f%+8.1f%% %s" % (r["name"], r["ind"], r["buy"], r["sell"], r["r_pct"], mark))
    print()

print("\n")
show(jun, "6/30")
show(jul, "7/17")
log("saved: " + OUT)
