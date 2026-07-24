#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S009 压力测试：2026年6-7月股灾期间表现分析
- 6月调仓日选了哪些票
- 7月调仓日换了哪些票（是否追高通信板块）
- 7/17股灾单日组合收益 vs 全市场
- 6月净值 vs 7月净值对比
"""
import json
import sqlite3
import time
import numpy as np
import pandas as pd
import lightgbm as lgb

BASE_DIR = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股"
PANEL_PATH = f"{BASE_DIR}/features_panel.pkl"
DB_PATH = "/Users/ziruzhu/stock-data/stock_all.db"

# 只跑最近6个月（2026年1月-7月），加快速度；训练窗口仍用过去12个月
FOCUS_START = "20260101"
TRAIN_MONTHS = 12
TOP_N = 20
LABEL_HORIZON = 10
BUY_COMMISSION = 0.00025
SELL_COMMISSION = 0.00025
STAMP_TAX = 0.0005

FEATURE_COLS = [
    "mom_5","mom_10","mom_20","mom_60","mom_120",
    "turnover_rate","turnover_rate_f","volume_ratio","vol_chg_20",
    "bias_5","bias_10","bias_20","bias_60",
    "macd_dif","macd_dea","macd","kdj_k","kdj_d","kdj_j",
    "rsi_6","rsi_12","rsi_24","cci","boll_pct","boll_width",
    "pe","pe_ttm","pb","ps","ps_ttm","dv_ttm",
    "net_mf_ratio","lg_buy_ratio",
]

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def load_blacklist():
    con = sqlite3.connect(DB_PATH)
    st = pd.read_sql("SELECT ts_code FROM blacklist_st", con)["ts_code"].tolist()
    loss = pd.read_sql("SELECT ts_code FROM blacklist_loss", con)["ts_code"].tolist()
    con.close()
    return set(st) | set(loss)

def load_stock_info():
    con = sqlite3.connect(DB_PATH)
    df = pd.read_sql("SELECT ts_code, name, industry FROM stock_list", con)
    con.close()
    return df.set_index("ts_code")

def get_month_start_dates(trade_dates):
    s = pd.Series(pd.to_datetime(trade_dates, format="%Y%m%d"))
    df = pd.DataFrame({"date": s, "trade_date": trade_dates})
    df["ym"] = df["date"].dt.to_period("M")
    firsts = df.groupby("ym").first()
    return firsts["trade_date"].tolist()

def train_and_score(train_df, score_df):
    X_train = train_df[FEATURE_COLS]
    y_train = train_df["label"]
    model = lgb.LGBMClassifier(
        boosting_type="gbdt", num_leaves=31, learning_rate=0.05,
        n_estimators=200, subsample=0.8, colsample_bytree=0.8,
        random_state=42, verbose=-1,
    )
    model.fit(X_train, y_train)
    scores = model.predict_proba(score_df[FEATURE_COLS])[:, 1]
    return scores

def main():
    t0 = time.time()
    log("读取特征面板...")
    panel = pd.read_pickle(PANEL_PATH)
    log(f"面板: {len(panel):,} 行")

    blacklist = load_blacklist()
    panel = panel[~panel["ts_code"].isin(blacklist)]
    panel = panel[~panel["ts_code"].str.endswith(".BJ")].reset_index(drop=True)
    panel = panel.dropna(subset=FEATURE_COLS, how="all")

    stock_info = load_stock_info()

    all_trade_dates = sorted(panel["trade_date"].unique())
    month_dates = get_month_start_dates(all_trade_dates)
    rebalance_dates = [d for d in month_dates if d >= FOCUS_START]
    log(f"焦点区间调仓日: {rebalance_dates}")

    panel_by_date = {d: sub for d, sub in panel.groupby("trade_date")}
    open_lookup = panel.set_index(["ts_code", "trade_date"])["open_qfq"].sort_index()
    close_lookup = panel.set_index(["ts_code", "trade_date"])["close_qfq"].sort_index()
    next_trade_date = {d: all_trade_dates[i+1] for i, d in enumerate(all_trade_dates) if i+1 < len(all_trade_dates)}

    # 需要用过去12个月数据训练，面板数据要包含更早期数据
    results = []
    prev_holdings = set()

    for i, rd in enumerate(rebalance_dates):
        rd_dt = pd.to_datetime(rd, format="%Y%m%d")
        train_start = (rd_dt - pd.DateOffset(months=TRAIN_MONTHS)).strftime("%Y%m%d")
        train_mask = (panel["trade_date"] >= train_start) & (panel["trade_date"] < rd)
        train_df = panel.loc[train_mask].dropna(subset=FEATURE_COLS + ["label"])
        if len(train_df) < 5000:
            log(f"[{rd}] 训练样本不足，跳过")
            continue

        score_df = panel_by_date.get(rd)
        if score_df is None:
            continue
        score_df = score_df.dropna(subset=FEATURE_COLS, how="all").copy()

        log(f"[{rd}] 训练样本: {len(train_df):,}, 评分股票: {len(score_df)}")
        scores = train_and_score(train_df, score_df)
        score_df["score"] = scores
        top20_df = score_df.sort_values("score", ascending=False).head(TOP_N)
        top20 = top20_df["ts_code"].tolist()

        # 加入股票名称和行业
        top20_info = []
        for code in top20:
            info = stock_info.loc[code] if code in stock_info.index else pd.Series({"name": "未知", "industry": "未知"})
            top20_info.append({
                "ts_code": code,
                "name": info.get("name", "未知"),
                "industry": info.get("industry", "未知"),
                "score": round(float(score_df[score_df["ts_code"]==code]["score"].values[0]), 4)
            })

        next_rd = rebalance_dates[i+1] if i+1 < len(rebalance_dates) else None
        if next_rd is None:
            # 最后一期：计算到当前最新交易日（7/17）的收益
            buy_date = next_trade_date.get(rd)
            sell_date = "20260717"  # 最新数据截止日
        else:
            buy_date = next_trade_date.get(rd)
            sell_date = next_trade_date.get(next_rd)

        if buy_date is None or sell_date is None:
            continue

        rets = []
        stock_rets = []
        for code in top20:
            try:
                p0 = open_lookup.loc[(code, buy_date)]
                p1 = close_lookup.loc[(code, sell_date)]  # 用最新收盘价
                if pd.isna(p0) or pd.isna(p1) or p0 <= 0:
                    continue
                r = float(p1) / float(p0) - 1
                info = stock_info.loc[code] if code in stock_info.index else pd.Series({"name":"","industry":""})
                stock_rets.append({
                    "ts_code": code,
                    "name": info.get("name",""),
                    "industry": info.get("industry",""),
                    "buy_price": round(float(p0), 2),
                    "sell_price": round(float(p1), 2),
                    "return_pct": round(r*100, 2)
                })
                rets.append(r)
            except KeyError:
                continue

        if not rets:
            continue

        gross_ret = float(np.mean(rets))
        curr_holdings = set(top20)
        bought = curr_holdings - prev_holdings
        sold = prev_holdings - curr_holdings
        n_curr = len(curr_holdings) or 1
        n_prev = len(prev_holdings) or 1
        buy_turnover = len(bought) / n_curr
        sell_turnover = len(sold) / n_prev if n_prev > 0 else 0
        total_cost = buy_turnover * BUY_COMMISSION + sell_turnover * (SELL_COMMISSION + STAMP_TAX)
        period_ret = gross_ret - total_cost

        # 行业分布
        industry_dist = {}
        for s in top20_info:
            ind = s["industry"]
            industry_dist[ind] = industry_dist.get(ind, 0) + 1

        results.append({
            "rebalance_date": rd,
            "buy_date": buy_date,
            "sell_date": sell_date,
            "period_label": f"{rd[:6]}→{sell_date[:6]}",
            "top20_holdings": top20_info,
            "industry_distribution": industry_dist,
            "stock_returns": sorted(stock_rets, key=lambda x: x["return_pct"], reverse=True),
            "gross_return_pct": round(gross_ret * 100, 2),
            "cost_pct": round(total_cost * 100, 4),
            "net_return_pct": round(period_ret * 100, 2),
            "win_count": sum(1 for r in rets if r > 0),
            "total_count": len(rets),
        })
        prev_holdings = curr_holdings
        log(f"[{rd}] 期间净收益: {period_ret*100:.2f}%, 持仓行业: {industry_dist}")

    # -------- 计算7/17单日组合表现 --------
    # 7月持仓 = 7月初调仓日选的票，用7/16收盘 → 7/17收盘
    jul_period = next((r for r in results if r["rebalance_date"].startswith("2026070")), None)
    if jul_period:
        jul_holdings = [s["ts_code"] for s in jul_period["top20_holdings"]]
        crash_rets = []
        crash_stocks = []
        for code in jul_holdings:
            try:
                p0 = close_lookup.loc[(code, "20260716")]
                p1 = close_lookup.loc[(code, "20260717")]
                if pd.isna(p0) or pd.isna(p1) or p0 <= 0:
                    continue
                r = float(p1)/float(p0) - 1
                info = stock_info.loc[code] if code in stock_info.index else pd.Series({"name":"","industry":""})
                crash_stocks.append({"ts_code": code, "name": info.get("name",""), "industry": info.get("industry",""), "crash_return_pct": round(r*100,2)})
                crash_rets.append(r)
            except KeyError:
                continue

        # 全市场7/17中位数收益
        market_7_16 = panel_by_date.get("20260716")
        market_7_17 = panel_by_date.get("20260717")
        market_crash_ret = None
        if market_7_16 is not None and market_7_17 is not None:
            merged = market_7_16[["ts_code","close_qfq"]].merge(
                market_7_17[["ts_code","close_qfq"]], on="ts_code", suffixes=("_16","_17"))
            merged = merged[(merged["close_qfq_16"] > 0) & (merged["close_qfq_17"] > 0)]
            merged["r"] = merged["close_qfq_17"] / merged["close_qfq_16"] - 1
            market_crash_ret = round(float(merged["r"].median()) * 100, 2)

        crash_result = {
            "date": "20260717",
            "s009_portfolio_return_pct": round(float(np.mean(crash_rets))*100, 2) if crash_rets else None,
            "market_median_return_pct": market_crash_ret,
            "stocks": sorted(crash_stocks, key=lambda x: x["crash_return_pct"])
        }
        log(f"7/17 股灾: S009组合 {crash_result['s009_portfolio_return_pct']}% vs 全市场中位数 {crash_result['market_median_return_pct']}%")
    else:
        crash_result = None
        log("未找到7月期数据，跳过7/17分析")

    output = {
        "strategy": "S009-LightGBM多因子选股",
        "test_scenario": "2026年6-7月 通信大涨→股灾压力测试",
        "periods": results,
        "crash_day_analysis": crash_result,
    }
    out_path = f"{BASE_DIR}/stress_test_jun_jul_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    log(f"结果已写出: {out_path}")
    log(f"总耗时: {time.time()-t0:.1f}s")

if __name__ == "__main__":
    main()
