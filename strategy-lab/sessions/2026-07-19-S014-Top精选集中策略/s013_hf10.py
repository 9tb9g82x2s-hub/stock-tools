#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S013 高频版：每10个交易日选股换仓，全程满仓
label = 10天（精确对齐持仓期），每10交易日调仓一次
对比：S013原版(label10天·月度22天) vs 本版(label10天·10天)
"""
import json, sqlite3, time, os, sys
import numpy as np, pandas as pd
import lightgbm as lgb

BASE_DIR = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S013-喜神池LightGBM选股"
OUT_DIR  = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S014-Top精选集中策略"
PANEL_PATH = f"{BASE_DIR}/features_panel.pkl"
DB_PATH = "/Users/ziruzhu/stock-data/stock_all.db"
XISHEN_PATH = f"{BASE_DIR}/xishen_plus_pool.csv"

HORIZON = 10          # label窗口=10天
HOLD_TDAYS = 10       # 持仓交易日=10天
BACKTEST_START = "20170101"
TRAIN_MONTHS = 12
TOP_N = 20
STOP_LOSS = -0.12
BC, SC = 0.00025, 0.00125

CKPT = f"{OUT_DIR}/ckpt_hf10.json"
LOG  = f"{OUT_DIR}/hf10_run.log"
OUT_FILE = f"{OUT_DIR}/s013_hf10_result.json"

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
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")

def load_blacklist():
    con = sqlite3.connect(DB_PATH)
    st = pd.read_sql("SELECT ts_code FROM blacklist_st", con)["ts_code"].tolist()
    loss = pd.read_sql("SELECT ts_code FROM blacklist_loss", con)["ts_code"].tolist()
    con.close()
    return set(st) | set(loss)

def main():
    t0 = time.time()
    log("="*60)
    log(f"S013高频版: label{HORIZON}天, 每{HOLD_TDAYS}交易日调仓")

    pool = pd.read_csv(XISHEN_PATH)
    xishen_set = set(pool["ts_code"])
    log(f"喜神池: {len(xishen_set)} 只")

    log("读取特征面板...")
    panel = pd.read_pickle(PANEL_PATH)

    # label = 未来10天跑赢中位数（和原版S013b完全一样，不用重算）
    log("使用原始label(未来10天)...")
    panel = panel.dropna(subset=["label"])
    blacklist = load_blacklist()
    panel = panel[~panel["ts_code"].isin(blacklist)].reset_index(drop=True)
    panel = panel[~panel["ts_code"].str.endswith(".BJ")].reset_index(drop=True)
    panel = panel.dropna(subset=FEATURE_COLS, how="all")
    log(f"面板清洗后: {len(panel):,} 行")

    all_trade_dates = sorted(panel["trade_date"].unique())
    next_trade_date = {d: all_trade_dates[i+1] for i, d in enumerate(all_trade_dates) if i+1 < len(all_trade_dates)}

    # 生成调仓日：从BACKTEST_START起，每HOLD_TDAYS个交易日取一个
    start_dates = [d for d in all_trade_dates if d >= BACKTEST_START]
    rebalance_dates = start_dates[::HOLD_TDAYS]
    log(f"调仓日: {len(rebalance_dates)}个 (每{HOLD_TDAYS}交易日), 首={rebalance_dates[0]} 末={rebalance_dates[-1]}")

    panel_by_date = {d: sub for d, sub in panel.groupby("trade_date")}
    open_lookup = panel.set_index(["ts_code", "trade_date"])["open_qfq"].sort_index()
    close_lookup = panel.set_index(["ts_code", "trade_date"])["close_qfq"].sort_index()

    trades, nav, nav_curve = [], 1.0, []
    prev_holdings = set()
    total_stops, total_checks = 0, 0
    resume_from = 0

    if os.path.exists(CKPT):
        try:
            ckpt = json.load(open(CKPT))
            trades=ckpt["trades"]; nav=ckpt["nav"]; nav_curve=ckpt["nav_curve"]
            total_stops=ckpt["total_stops"]; total_checks=ckpt["total_checks"]
            resume_from=ckpt["next_i"]; prev_holdings=set(ckpt.get("prev_holdings",[]))
            log(f"[断点续跑] 从第{resume_from}期 (已{len(trades)}期, nav={nav:.4f})")
        except Exception as e:
            log(f"[断点]读取失败({e}),从头")
            resume_from = 0

    for i, rd in enumerate(rebalance_dates):
        if i < resume_from:
            continue
        rd_dt = pd.to_datetime(rd, format="%Y%m%d")
        train_start = (rd_dt - pd.DateOffset(months=TRAIN_MONTHS)).strftime("%Y%m%d")
        train_mask = (panel["trade_date"] >= train_start) & (panel["trade_date"] < rd)
        train_df = panel.loc[train_mask].dropna(subset=FEATURE_COLS + ["label"])
        if len(train_df) < 5000:
            continue

        score_df_raw = panel_by_date.get(rd)
        if score_df_raw is None or len(score_df_raw) == 0:
            continue
        score_df = score_df_raw[score_df_raw["ts_code"].isin(xishen_set)].dropna(subset=FEATURE_COLS, how="all").copy()
        if len(score_df) < TOP_N:
            continue

        model = lgb.LGBMClassifier(
            boosting_type="gbdt", num_leaves=31, learning_rate=0.05,
            n_estimators=200, subsample=0.8, colsample_bytree=0.8,
            random_state=42, verbose=-1)
        model.fit(train_df[FEATURE_COLS], train_df["label"])
        scores = model.predict_proba(score_df[FEATURE_COLS])[:, 1]
        score_df["score"] = scores
        top20 = score_df.sort_values("score", ascending=False).head(TOP_N)["ts_code"].tolist()

        next_rd = rebalance_dates[i+1] if i+1 < len(rebalance_dates) else None
        if next_rd is None:
            break
        buy_date = next_trade_date.get(rd)
        sell_date = next_trade_date.get(next_rd)
        if buy_date is None or sell_date is None:
            continue

        rets, valid_holdings, stop_count = [], [], 0
        for code in top20:
            try:
                entry_px = open_lookup.loc[(code, buy_date)]
                if pd.isna(entry_px) or entry_px <= 0:
                    continue
                stop_hit, stop_px, d = False, None, buy_date
                while d < sell_date:
                    d_next = next_trade_date.get(d)
                    if d_next is None:
                        break
                    try:
                        cl = close_lookup.loc[(code, d_next)]
                    except KeyError:
                        d = d_next; continue
                    if not pd.isna(cl) and cl > 0 and cl / entry_px - 1 <= STOP_LOSS:
                        stop_hit, stop_px = True, cl; break
                    d = d_next
                if stop_hit:
                    r = float(stop_px)/float(entry_px) - 1; stop_count += 1
                else:
                    try:
                        exit_px = open_lookup.loc[(code, sell_date)]
                    except KeyError:
                        continue
                    if pd.isna(exit_px) or exit_px <= 0:
                        continue
                    r = float(exit_px)/float(entry_px) - 1
                rets.append(r); valid_holdings.append(code)
            except KeyError:
                continue

        total_stops += stop_count; total_checks += len(valid_holdings)
        if len(rets) == 0:
            continue
        gross_ret = float(np.mean(rets))
        curr = set(valid_holdings)
        bought = curr - prev_holdings; sold = prev_holdings - curr
        nc = len(curr) if curr else 1; np_ = len(prev_holdings) if prev_holdings else 1
        bt = len(bought)/nc; st_ = len(sold)/np_ if np_ > 0 else 0
        cost = bt * BC + st_ * (SC + 0.0005)
        net_ret = gross_ret - cost
        nav *= (1 + net_ret)
        nav_curve.append({"date": sell_date, "nav": round(nav, 6)})
        trades.append({
            "rebalance_date": rd, "buy_date": buy_date, "sell_date": sell_date,
            "holdings": valid_holdings, "n_holdings": len(valid_holdings),
            "period_return": round(net_ret, 6), "stop_count": stop_count,
            "win_count": int(sum(1 for r in rets if r > 0))
        })
        prev_holdings = curr

        if len(trades) % 24 == 0:
            log(f"已完成 {i+1}/{len(rebalance_dates)} 期 ({rd}), nav:{nav:.4f}")
            json.dump({"trades":trades,"nav":nav,"nav_curve":nav_curve,
                       "total_stops":total_stops,"total_checks":total_checks,
                       "next_i":i+1,"prev_holdings":list(prev_holdings)},
                      open(CKPT,"w"),ensure_ascii=False,default=str)

    # 汇总
    pr = np.array([t["period_return"] for t in trades])
    n_years = (pd.to_datetime(trades[-1]["sell_date"]) - pd.to_datetime(trades[0]["buy_date"])).days/365.25
    nav_arr = np.array([1.0]+[c["nav"] for c in nav_curve])
    dd = (nav_arr/np.maximum.accumulate(nav_arr)-1).min()
    ann = nav**(1/n_years)-1 if n_years>0 and nav>0 else 0
    wr = float(np.mean(pr>0))
    ppy = 252/HOLD_TDAYS  # 每年约25次
    sh = float(pr.mean()/pr.std()*np.sqrt(ppy)) if pr.std()>0 else 0

    log(f"[结果] 期数{len(trades)} 年化{ann*100:.2f}% 回撤{dd*100:.2f}% 夏普{sh:.2f} 胜率{wr*100:.1f}%")
    log(f"[对比] S013原版: 年化36.6% 回撤-14.3% | S017(20天月度): 年化51.6%")

    result = {
        "config":{"horizon":HORIZON,"hold_tdays":HOLD_TDAYS,"top_n":TOP_N,"stop_loss":STOP_LOSS},
        "metrics":{"annual_return":round(ann,4),"max_drawdown":round(float(dd),4),
                   "sharpe":round(sh,4),"win_rate":round(wr,4),
                   "total_return":round(float(nav-1),4),"n_periods":len(trades)},
        "nav_curve":nav_curve,"trades":trades
    }
    json.dump(result,open(OUT_FILE,"w"),ensure_ascii=False,indent=2,default=str)
    log(f"结果已写出: {OUT_FILE}  耗时{time.time()-t0:.0f}s")
    if os.path.exists(CKPT):
        os.remove(CKPT)

if __name__ == "__main__":
    main()
