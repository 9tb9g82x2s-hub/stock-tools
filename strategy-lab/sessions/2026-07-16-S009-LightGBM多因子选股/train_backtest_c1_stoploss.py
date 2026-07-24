#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S009-c1 -12%止损版 回测脚本

在v1.3(沪深两市+北交所除外+T+1开盘价+手续费印花税)基础上，
新增【单股-12%止损】机制：
- 持仓期间，若某只股票从买入价回撤超过12%(前复权收盘价跌破买入价×0.88)，
  则以该日收盘价止损退出，不再持有到下次调仓
- 止损股退出后资金不再重新配置，等权计算组合收益时该股按实际持有天数收益计入
- 其余全部逻辑与v1.3一致
"""
import json, sqlite3, time
import numpy as np, pandas as pd
import lightgbm as lgb

BASE_DIR = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股"
PANEL_PATH = f"{BASE_DIR}/features_panel.pkl"
DB_PATH = "/Users/ziruzhu/stock-data/stock_all.db"

BACKTEST_START = "20170101"
TRAIN_MONTHS = 12
TOP_N = 20
STOP_LOSS = -0.12

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

def get_month_start_dates(trade_dates):
    s = pd.Series(pd.to_datetime(trade_dates, format="%Y%m%d"))
    df = pd.DataFrame({"date":s,"trade_date":trade_dates})
    df["ym"] = df["date"].dt.to_period("M")
    return df.groupby("ym").first()["trade_date"].tolist()

def main():
    t0 = time.time()
    log("读取特征面板...")
    panel = pd.read_pickle(PANEL_PATH)

    blacklist = load_blacklist()
    panel = panel[~panel["ts_code"].isin(blacklist)].reset_index(drop=True)
    panel = panel[~panel["ts_code"].str.endswith(".BJ")].reset_index(drop=True)
    panel = panel.dropna(subset=FEATURE_COLS, how="all")

    all_trade_dates = sorted(panel["trade_date"].unique())
    month_dates = get_month_start_dates(all_trade_dates)
    rebalance_dates = [d for d in month_dates if d >= BACKTEST_START]
    log(f"调仓日: {len(rebalance_dates)}, 首={rebalance_dates[0]}, 末={rebalance_dates[-1]}")

    panel_by_date = {d:sub for d,sub in panel.groupby("trade_date")}
    open_lookup = panel.set_index(["ts_code","trade_date"])["open_qfq"].sort_index()
    close_lookup = panel.set_index(["ts_code","trade_date"])["close_qfq"].sort_index()
    next_trade_date = {d:all_trade_dates[i+1] for i,d in enumerate(all_trade_dates) if i+1<len(all_trade_dates)}

    nav = 1.0
    nav_curve, trades = [], []
    prev_holdings = set()
    total_stops, total_checks = 0, 0

    for i, rd in enumerate(rebalance_dates):
        rd_dt = pd.to_datetime(rd, format="%Y%m%d")
        train_start = (rd_dt-pd.DateOffset(months=TRAIN_MONTHS)).strftime("%Y%m%d")
        train_mask = (panel["trade_date"]>=train_start)&(panel["trade_date"]<rd)
        train_df = panel.loc[train_mask].dropna(subset=FEATURE_COLS+["label"])
        if len(train_df) < 5000: continue

        score_df = panel_by_date.get(rd)
        if score_df is None or len(score_df)==0: continue
        score_df = score_df.dropna(subset=FEATURE_COLS, how="all").copy()

        model = lgb.LGBMClassifier(boosting_type="gbdt",num_leaves=31,learning_rate=0.05,
            n_estimators=200,subsample=0.8,colsample_bytree=0.8,random_state=42,verbose=-1)
        model.fit(train_df[FEATURE_COLS], train_df["label"])
        scores = model.predict_proba(score_df[FEATURE_COLS])[:,1]
        score_df["score"] = scores
        top20 = score_df.sort_values("score",ascending=False).head(TOP_N)["ts_code"].tolist()

        next_rd = rebalance_dates[i+1] if i+1<len(rebalance_dates) else None
        if next_rd is None: break
        buy_date = next_trade_date.get(rd)
        sell_date = next_trade_date.get(next_rd)
        if buy_date is None or sell_date is None: continue

        rets, valid_holdings = [], []
        stop_count = 0

        for code in top20:
            try:
                entry_px = open_lookup.loc[(code, buy_date)]
                if pd.isna(entry_px) or entry_px<=0: continue

                stop_hit = False
                stop_px = None

                # 逐日检查止损：从buy_date到sell_date-1，看是否有收盘价跌破-12%
                d = buy_date
                while d < sell_date:
                    d_next = next_trade_date.get(d)
                    if d_next is None: break
                    try:
                        cl = close_lookup.loc[(code, d_next)]
                    except KeyError:
                        d = d_next
                        continue
                    if not pd.isna(cl) and cl>0 and cl/entry_px-1 <= STOP_LOSS:
                        stop_hit = True
                        stop_px = cl
                        break
                    d = d_next

                if stop_hit:
                    r = float(stop_px)/float(entry_px)-1
                    stop_count += 1
                else:
                    try:
                        exit_px = open_lookup.loc[(code, sell_date)]
                    except KeyError:
                        continue
                    if pd.isna(exit_px) or exit_px<=0: continue
                    r = float(exit_px)/float(entry_px)-1

                rets.append(r)
                valid_holdings.append(code)
            except KeyError:
                continue

        total_stops += stop_count
        total_checks += len(valid_holdings)
        if len(rets)==0: continue

        gross_ret = float(np.mean(rets))
        curr_holdings = set(valid_holdings)
        bought = curr_holdings-prev_holdings
        sold = prev_holdings-curr_holdings
        n_curr = len(curr_holdings) if curr_holdings else 1
        n_prev = len(prev_holdings) if prev_holdings else 1
        buy_turnover = len(bought)/n_curr
        sell_turnover = len(sold)/n_prev if n_prev>0 else 0.0
        cost = buy_turnover*BUY_COMMISSION + sell_turnover*(SELL_COMMISSION+STAMP_TAX)
        net_ret = gross_ret - cost
        nav *= (1+net_ret)
        nav_curve.append({"date":sell_date,"nav":round(nav,6)})
        trades.append({"rebalance_date":rd,"buy_date":buy_date,"next_date":next_rd,
            "sell_date":sell_date,"holdings":valid_holdings,"n_holdings":len(valid_holdings),
            "gross_return":round(gross_ret,6),"trading_cost":round(cost,6),
            "period_return":round(net_ret,6),
            "buy_turnover":round(buy_turnover,4),"sell_turnover":round(sell_turnover,4),
            "win_count":int(sum(1 for r in rets if r>0)),"stop_count":stop_count})
        prev_holdings = curr_holdings
        if (i+1)%12==0:
            log(f"已完成 {i+1}/{len(rebalance_dates)} 期 (最近:{rd}), 净值:{nav:.4f}")

    log(f"共{len(trades)}期, 最终净值:{nav:.4f}")
    log(f"止损统计: {total_stops}/{total_checks} 次触发止损, 比率{total_stops/total_checks*100:.1f}%")

    period_rets = np.array([t["period_return"] for t in trades])
    gross_rets = np.array([t["gross_return"] for t in trades])
    costs = np.array([t["trading_cost"] for t in trades])
    n_periods = len(period_rets)
    nav_arr = np.array([1.0]+[c["nav"] for c in nav_curve])
    running_max = np.maximum.accumulate(nav_arr)
    dd = nav_arr/running_max-1
    max_dd = float(dd.min())
    n_years = n_periods/12.0 if n_periods>0 else 1
    ann_ret = (nav**(1/n_years)-1) if n_years>0 and nav>0 else 0.0
    gross_nav = float(np.prod(1+gross_rets)) if len(gross_rets)>0 else 1.0
    gross_ann = (gross_nav**(1/n_years)-1) if n_years>0 and gross_nav>0 else 0.0
    win_rate = float(np.mean(period_rets>0))
    sharpe = float(period_rets.mean()/period_rets.std()*np.sqrt(12)) if period_rets.std()>0 else 0.0
    avg_turnover = float(np.mean([(t["buy_turnover"]+t["sell_turnover"])/2 for t in trades]))

    log(f"年化:{ann_ret*100:.2f}%(毛{gross_ann*100:.2f}%) 胜率:{win_rate*100:.1f}% 最大回撤:{max_dd*100:.2f}% 夏普:{sharpe:.2f}")

    result = {
        "strategy_name":"S009-c1 -12%止损版",
        "metrics":{"total_return":round(float(nav-1),4),"annual_return":round(float(ann_ret),4),
            "win_rate":round(win_rate,4),"max_drawdown":round(max_dd,4),
            "sharpe_ratio":round(sharpe,4),"total_trades":n_periods},
        "cost_analysis":{"gross_annual_return":round(float(gross_ann),4),
            "net_annual_return":round(float(ann_ret),4),
            "cost_drag_annualized":round(float(gross_ann-ann_ret),4),
            "avg_turnover_per_period":round(avg_turnover,4)},
        "stop_loss":{"threshold":STOP_LOSS,"total_stops":total_stops,"total_checks":total_checks,
            "stop_rate":round(total_stops/total_checks,4) if total_checks>0 else 0},
        "nav_curve":nav_curve,"trades_summary":trades[-24:],
        "stocks":[{"code":c,"signal_date":trades[-1]["rebalance_date"]} for c in trades[-1]["holdings"]] if trades else [],
    }
    with open(f"{BASE_DIR}/results_c1_stoploss.json","w",encoding="utf-8") as f:
        json.dump(result,f,ensure_ascii=False,indent=2)
    pd.DataFrame(trades).to_csv(f"{BASE_DIR}/trades_full_c1_stoploss.csv",index=False,encoding="utf-8-sig")
    log(f"结果已写出, 耗时{time.time()-t0:.1f}s")

if __name__=="__main__": main()
