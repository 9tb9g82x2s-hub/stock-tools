#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S023 周度5天换仓策略（基于S019重新训练）
================================================
与S019(高频10日换仓,年化64.5%)的差异：
  1. 【重新训练】label窗口 10天 -> 5天：预测未来5天是否跑赢横截面中位数
  2. 【持有周期5天/锚定自然周】不再用"每10交易日滚动"，改成按自然周：
       - 上周最后一个交易日(周五)收盘后打分选股
       - 本周第一个交易日(周一)开盘买入
       - 本周最后一个交易日(周五)收盘卖出
       - 周末空仓
  3. 其余S019固化规则全部继承：
       喜神池候选 / 股价≤500元过滤 / 止损-12% / 黑名单 / 剔北交所 / Top20等权满仓 / 成本模型

买卖价口径说明（与S019区别）：
  S019: 开盘买、开盘卖（open->open）
  S023: 开盘买、收盘卖（open->close，因为"周五收盘前卖"是老大明确要求）
断点续跑：按周checkpoint。
"""
import json, sqlite3, time, os, sys
import numpy as np, pandas as pd
import lightgbm as lgb

BASE_DIR = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S013-喜神池LightGBM选股"
OUT_DIR  = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-26-S023-周度5天换仓"
PANEL_PATH = f"{BASE_DIR}/features_panel.pkl"
DB_PATH = "/Users/ziruzhu/stock-data/stock_all.db"
XISHEN_PATH = f"{BASE_DIR}/xishen_plus_pool.csv"

HORIZON = 5           # ★label窗口=5天（重新训练的核心）
BACKTEST_START = "20170101"
TRAIN_MONTHS = 12
TOP_N = 20
STOP_LOSS = -0.12
PRICE_LIMIT = 500     # 只选真实股价≤500元的股
BC, SC = 0.00025, 0.00125

# 命令行可传 end_date 做小样本自测：python train_backtest_s022.py 20180101
END_LIMIT = sys.argv[1] if len(sys.argv) > 1 else "99999999"
TAG = sys.argv[2] if len(sys.argv) > 2 else "full"

CKPT = f"{OUT_DIR}/ckpt_{TAG}.json"
LOG  = f"{OUT_DIR}/run_{TAG}.log"
OUT_FILE = f"{OUT_DIR}/s023_result_{TAG}.json"

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
    log(f"S023周度5天换仓: label{HORIZON}天重训, 上周五打分->周一open买->周五close卖")

    pool = pd.read_csv(XISHEN_PATH)
    xishen_set = set(pool["ts_code"])
    log(f"喜神池: {len(xishen_set)} 只")

    log("读取特征面板...")
    panel = pd.read_pickle(PANEL_PATH)

    # ★重新训练：label窗口改为5天（未来5交易日跑赢横截面中位数）
    log(f"重算label(未来{HORIZON}天跑赢横截面中位数)...")
    panel = panel.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    g = panel.groupby("ts_code", group_keys=False)
    panel["fwd_ret"] = g["close_qfq"].transform(lambda s: s.shift(-HORIZON)/s - 1)
    med = panel.groupby("trade_date")["fwd_ret"].transform("median")
    panel["label"] = (panel["fwd_ret"] > med).astype("Int8")

    panel = panel.dropna(subset=["label"])
    blacklist = load_blacklist()
    panel = panel[~panel["ts_code"].isin(blacklist)].reset_index(drop=True)
    panel = panel[~panel["ts_code"].str.endswith(".BJ")].reset_index(drop=True)
    panel = panel.dropna(subset=FEATURE_COLS, how="all")
    log(f"面板清洗后: {len(panel):,} 行")

    all_trade_dates = sorted(panel["trade_date"].unique())

    # ★按自然周分组：每周取(首个交易日=周一买, 末个交易日=周五卖, 上周末个交易日=打分日)
    dts = pd.to_datetime(pd.Series(all_trade_dates), format="%Y%m%d")
    iso = dts.dt.isocalendar()
    wk_key = (iso["year"].astype(str) + "-" + iso["week"].astype(str).str.zfill(2)).values
    week_map = {}  # wk -> [dates...]
    for d, w in zip(all_trade_dates, wk_key):
        week_map.setdefault(w, []).append(d)
    week_keys = sorted(week_map.keys())

    # 生成每周的(score_date, buy_date, sell_date)
    weeks = []  # list of dict
    for wi, wk in enumerate(week_keys):
        days = week_map[wk]
        buy_date = days[0]          # 本周第一个交易日 -> 周一开盘买
        sell_date = days[-1]        # 本周最后一个交易日 -> 周五收盘卖
        if wi == 0:
            continue                # 第一周无上周打分日，跳过
        prev_days = week_map[week_keys[wi-1]]
        score_date = prev_days[-1]  # 上周最后一个交易日收盘 -> 打分选股（无未来函数）
        if buy_date < BACKTEST_START:
            continue
        if buy_date > END_LIMIT:
            break
        weeks.append({"wk": wk, "score_date": score_date,
                      "buy_date": buy_date, "sell_date": sell_date})
    log(f"回测周数: {len(weeks)}  首周买={weeks[0]['buy_date']} 末周卖={weeks[-1]['sell_date']}")

    # 预加载打分日真实不复权收盘价（股价≤500过滤用）
    score_dates = [w["score_date"] for w in weeks]
    _con = sqlite3.connect(DB_PATH)
    _sp = ",".join(f"'{d}'" for d in set(score_dates))
    _rpx = pd.read_sql(f"SELECT ts_code,trade_date,close FROM daily WHERE trade_date IN ({_sp})", _con)
    _con.close()
    _rpx["close"] = pd.to_numeric(_rpx["close"], errors="coerce")
    _rpx["trade_date"] = _rpx["trade_date"].astype(str)
    real_px_idx = _rpx.set_index(["trade_date","ts_code"])["close"]

    panel_by_date = {d: sub for d, sub in panel.groupby("trade_date")}
    open_lookup = panel.set_index(["ts_code", "trade_date"])["open_qfq"].sort_index()
    close_lookup = panel.set_index(["ts_code", "trade_date"])["close_qfq"].sort_index()
    next_trade_date = {d: all_trade_dates[i+1] for i, d in enumerate(all_trade_dates) if i+1 < len(all_trade_dates)}

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
            log(f"[断点续跑] 从第{resume_from}周 (已{len(trades)}周, nav={nav:.4f})")
        except Exception as e:
            log(f"[断点]读取失败({e}),从头")
            resume_from = 0

    for i, w in enumerate(weeks):
        if i < resume_from:
            continue
        score_date = w["score_date"]; buy_date = w["buy_date"]; sell_date = w["sell_date"]
        sd_dt = pd.to_datetime(score_date, format="%Y%m%d")
        train_start = (sd_dt - pd.DateOffset(months=TRAIN_MONTHS)).strftime("%Y%m%d")
        # 训练集：只用打分日之前的数据，且label窗口(5天)已实现（fwd_ret非NaN自动保证）
        train_mask = (panel["trade_date"] >= train_start) & (panel["trade_date"] < score_date)
        train_df = panel.loc[train_mask].dropna(subset=FEATURE_COLS + ["label"])
        if len(train_df) < 5000:
            continue

        score_df_raw = panel_by_date.get(score_date)
        if score_df_raw is None or len(score_df_raw) == 0:
            continue
        score_df = score_df_raw[score_df_raw["ts_code"].isin(xishen_set)].dropna(subset=FEATURE_COLS, how="all").copy()
        # 股价≤500过滤（打分日真实收盘价）
        if PRICE_LIMIT < 99999:
            try:
                _day_px = real_px_idx.loc[score_date]
                _keep = score_df["ts_code"].map(_day_px)
                score_df = score_df[(_keep <= PRICE_LIMIT) & (_keep.notna())]
            except KeyError:
                pass
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

        rets, valid_holdings, stop_count = [], [], 0
        for code in top20:
            try:
                entry_px = open_lookup.loc[(code, buy_date)]   # 周一开盘买
                if pd.isna(entry_px) or entry_px <= 0:
                    continue
                # 逐日盘中检查止损（buy_date次日起到sell_date当天收盘）
                stop_hit, stop_px, d = False, None, buy_date
                while d < sell_date:
                    d_next = next_trade_date.get(d)
                    if d_next is None or d_next > sell_date:
                        break
                    try:
                        cl = close_lookup.loc[(code, d_next)]
                    except KeyError:
                        d = d_next; continue
                    if not pd.isna(cl) and cl > 0 and cl/entry_px - 1 <= STOP_LOSS:
                        stop_hit, stop_px = True, cl; break
                    d = d_next
                if stop_hit:
                    r = float(stop_px)/float(entry_px) - 1; stop_count += 1
                else:
                    try:
                        exit_px = close_lookup.loc[(code, sell_date)]  # ★周五收盘卖
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
        # 周末空仓 -> 每周全买全卖，换手率高：买入=新建全部，卖出=上周全部
        curr = set(valid_holdings)
        bought = curr - prev_holdings; sold = prev_holdings - curr
        nc = len(curr) if curr else 1; np_ = len(prev_holdings) if prev_holdings else 1
        bt = len(bought)/nc; st_ = len(sold)/np_ if np_ > 0 else 0
        cost = bt * BC + st_ * (SC + 0.0005)
        net_ret = gross_ret - cost
        nav *= (1 + net_ret)
        nav_curve.append({"date": sell_date, "nav": round(nav, 6)})
        trades.append({
            "week": w["wk"], "score_date": score_date, "buy_date": buy_date, "sell_date": sell_date,
            "holdings": valid_holdings, "n_holdings": len(valid_holdings),
            "period_return": round(net_ret, 6), "stop_count": stop_count,
            "win_count": int(sum(1 for r in rets if r > 0))
        })
        prev_holdings = curr

        if len(trades) % 24 == 0:
            log(f"已完成 {i+1}/{len(weeks)} 周 ({buy_date}), nav:{nav:.4f}")
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
    ppy = 52  # 每年约52周
    sh = float(pr.mean()/pr.std()*np.sqrt(ppy)) if pr.std()>0 else 0

    log(f"[结果] 周数{len(trades)} 年化{ann*100:.2f}% 回撤{dd*100:.2f}% 夏普{sh:.2f} 胜率{wr*100:.1f}%")
    log(f"[对比] S019(10日滚动): 年化64.5% 回撤-12.2% 夏普2.17 胜率70.0%")

    result = {
        "config":{"horizon":HORIZON,"rebalance":"weekly","buy":"mon_open","sell":"fri_close",
                  "top_n":TOP_N,"stop_loss":STOP_LOSS,"price_limit":PRICE_LIMIT},
        "metrics":{"annual_return":round(ann,4),"max_drawdown":round(float(dd),4),
                   "sharpe":round(sh,4),"win_rate":round(wr,4),
                   "total_return":round(float(nav-1),4),"n_periods":len(trades),
                   "total_stops":total_stops,"total_checks":total_checks},
        "nav_curve":nav_curve,"trades":trades
    }
    json.dump(result,open(OUT_FILE,"w"),ensure_ascii=False,indent=2,default=str)
    log(f"结果已写出: {OUT_FILE}  耗时{time.time()-t0:.0f}s")
    if os.path.exists(CKPT):
        os.remove(CKPT)

if __name__ == "__main__":
    main()

