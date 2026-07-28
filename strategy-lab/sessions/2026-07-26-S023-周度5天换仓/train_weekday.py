#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S023 真·星期几效应测试
================================================================================
问题：固定每周某个星期几买入、持有5个交易日卖出，收益/回撤/胜率差异？
与之前Phase测试的区别：
  - Phase测试是"每5交易日滚动"，买入日在5个星期几间漂移（周三只占27%），
    不是纯粹的星期几效应
  - 本测试：强制只在目标星期几(周一~周五)买入，前一交易日收盘打分选股，
    买入后持有5个交易日(next 5 trading days)后卖出。这才是真正的"星期几择时"

命令行：python3 train_weekday.py <weekday 0-4>   0=周一...4=周五
并行：主控模式并行拉起5个worker(周一~周五各一个)
输出：s023_weekday_result.json（合并5个星期几）
"""
import json, sqlite3, time, os, sys, pickle
import numpy as np, pandas as pd
import lightgbm as lgb

if len(sys.argv) < 2:
    print("用法: python3 train_weekday.py <weekday 0-4 | master>")
    sys.exit(1)

MODE = sys.argv[1]  # "master" 或 0~4
WORKER_WD = None if MODE == "master" else int(MODE)

BASE_DIR = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S013-喜神池LightGBM选股"
OUT_DIR  = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-26-S023-周度5天换仓"
PANEL_PATH = f"{BASE_DIR}/features_panel.pkl"
DB_PATH = "/Users/ziruzhu/stock-data/stock_all.db"
XISHEN_PATH = f"{BASE_DIR}/xishen_plus_pool.csv"

HORIZON = 5
HOLD_DAYS = 5
BACKTEST_START = "20170101"
DATA_END = "20260717"
TRAIN_MONTHS = 12
TOP_N = 20
PRICE_LIMIT = 500
BC, SC = 0.00025, 0.00125
STOP_LOSS = None  # 不设止损，最干净口径（与Phase对比的None列一致）
LGB_THREADS = 4

WD_NAME = ['周一','周二','周三','周四','周五']
LOG = f"{OUT_DIR}/weekday_{MODE}_run.log"
OUT_FILE = f"{OUT_DIR}/s023_weekday_result.json"

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
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def load_blacklist():
    con = sqlite3.connect(DB_PATH)
    st = pd.read_sql("SELECT ts_code FROM blacklist_st", con)["ts_code"].tolist()
    loss = pd.read_sql("SELECT ts_code FROM blacklist_loss", con)["ts_code"].tolist()
    con.close()
    return set(st) | set(loss)

def load_data():
    pool = pd.read_csv(XISHEN_PATH)
    xishen_set = set(pool["ts_code"])
    panel = pd.read_pickle(PANEL_PATH)
    panel = panel.sort_values(["ts_code","trade_date"]).reset_index(drop=True)
    g = panel.groupby("ts_code", group_keys=False)
    panel["fwd_ret"] = g["close_qfq"].transform(lambda s: s.shift(-HORIZON)/s-1)
    med = panel.groupby("trade_date")["fwd_ret"].transform("median")
    panel["label"] = (panel["fwd_ret"] > med).astype("Int8")
    panel = panel.dropna(subset=["label"])
    bl = load_blacklist()
    panel = panel[~panel["ts_code"].isin(bl)].reset_index(drop=True)
    panel = panel[~panel["ts_code"].str.endswith(".BJ")].reset_index(drop=True)
    panel = panel.dropna(subset=FEATURE_COLS, how="all")
    panel = panel[panel["trade_date"] <= DATA_END].reset_index(drop=True)
    all_dates = sorted(panel["trade_date"].unique())
    all_dates = [d for d in all_dates if d >= BACKTEST_START]
    _con = sqlite3.connect(DB_PATH)
    _rp = ",".join(f"'{d}'" for d in all_dates)
    _rpx = pd.read_sql(f"SELECT ts_code,trade_date,close FROM daily WHERE trade_date IN ({_rp})", _con)
    _con.close()
    _rpx["close"] = pd.to_numeric(_rpx["close"], errors="coerce")
    _rpx["trade_date"] = _rpx["trade_date"].astype(str)
    real_px_idx = _rpx.set_index(["trade_date","ts_code"])["close"]
    open_lookup = panel.set_index(["ts_code","trade_date"])["open_qfq"].sort_index()
    close_lookup = panel.set_index(["ts_code","trade_date"])["close_qfq"].sort_index()
    ntd = {d: all_dates[i+1] for i,d in enumerate(all_dates) if i+1<len(all_dates)}
    ptd = {all_dates[i+1]: d for i,d in enumerate(all_dates) if i+1<len(all_dates)}  # 前一交易日
    return xishen_set, panel, all_dates, open_lookup, close_lookup, ntd, ptd, real_px_idx


def run_worker(wd):
    """跑固定星期wd买入的回测：每周该星期几买入，持有HOLD_DAYS个交易日卖出"""
    ck = f"{OUT_DIR}/wd_signals_{wd}.pkl"
    if os.path.exists(ck):
        log(f"[{WD_NAME[wd]}] 已有缓存")
        return
    t0 = time.time()
    log(f"[{WD_NAME[wd]}] 启动，加载数据...")
    xishen_set, panel, all_dates, open_lookup, close_lookup, ntd, ptd, real_px_idx = load_data()
    panel_by_date = {d: sub for d, sub in panel.groupby("trade_date")}

    # 买入日 = 星期几==wd 的所有交易日
    dts = pd.to_datetime(pd.Series(all_dates), format="%Y%m%d")
    buy_days = [d for d, w in zip(all_dates, dts.dt.dayofweek) if w == wd]
    log(f"[{WD_NAME[wd]}] 候选买入日 {len(buy_days)} 个")

    signals = []
    for k, buy_date in enumerate(buy_days):
        # 打分日 = 买入日的前一交易日
        score_date = ptd.get(buy_date)
        if score_date is None:
            continue
        # 卖出日 = 买入日后第HOLD_DAYS个交易日
        try:
            bi = all_dates.index(buy_date)
        except ValueError:
            continue
        si = bi + HOLD_DAYS
        if si >= len(all_dates):
            continue
        sell_date = all_dates[si]

        # 训练
        sd_dt = pd.to_datetime(score_date, format="%Y%m%d")
        train_start = (sd_dt - pd.DateOffset(months=TRAIN_MONTHS)).strftime("%Y%m%d")
        train_mask = (panel["trade_date"] >= train_start) & (panel["trade_date"] < score_date)
        train_df = panel.loc[train_mask].dropna(subset=FEATURE_COLS + ["label"])
        if len(train_df) < 5000:
            continue
        score_df_raw = panel_by_date.get(score_date)
        if score_df_raw is None or len(score_df_raw) == 0:
            continue
        score_df = score_df_raw[score_df_raw["ts_code"].isin(xishen_set)].dropna(subset=FEATURE_COLS, how="all").copy()
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
            random_state=42, verbose=-1, n_jobs=LGB_THREADS)
        model.fit(train_df[FEATURE_COLS], train_df["label"])
        scores = model.predict_proba(score_df[FEATURE_COLS])[:, 1]
        score_df["score"] = scores
        top = score_df.sort_values("score", ascending=False).head(TOP_N)["ts_code"].tolist()

        # 计算这批票的收益（open买入 → sell_date close卖出，不设止损）
        stocks = []
        for code in top:
            try:
                entry = open_lookup.loc[(code, buy_date)]
                if pd.isna(entry) or entry <= 0:
                    continue
                exit_px = close_lookup.loc[(code, sell_date)]
                if pd.isna(exit_px) or exit_px <= 0:
                    continue
                stocks.append(float(exit_px)/float(entry) - 1)
            except KeyError:
                continue
        if not stocks:
            continue
        signals.append({"buy_date": buy_date, "sell_date": sell_date, "ret": float(np.mean(stocks)), "n": len(stocks)})

        if (k+1) % 30 == 0:
            log(f"[{WD_NAME[wd]}] {k+1}/{len(buy_days)}期, {(time.time()-t0)/60:.1f}分")

    pickle.dump(signals, open(ck, "wb"))
    log(f"[{WD_NAME[wd]}] 完成 {len(signals)}期, {(time.time()-t0)/60:.1f}分")


def compute_metrics(signals):
    """按周度序列算指标。相邻两周同一星期几之间换仓，成本简化为每期全换"""
    nav = 1.0
    nav_curve = []
    prev = set()
    rets = []
    for s in signals:
        # 成本：假设每期全买全卖（不同星期几持仓不重叠）
        cost = BC + (SC + 0.0005)
        net = s["ret"] - cost
        nav *= (1 + net)
        nav_curve.append({"date": s["sell_date"], "nav": nav})
        rets.append(net)
    rets = np.array(rets)
    dates = [c["date"] for c in nav_curve]
    n_years = (pd.to_datetime(dates[-1]) - pd.to_datetime(dates[0])).days / 365.25
    arr = np.array([1.0] + [c["nav"] for c in nav_curve])
    dd = (arr / np.maximum.accumulate(arr) - 1).min()
    ann = nav ** (1/n_years) - 1 if n_years > 0 and nav > 0 else 0
    wr = float(np.mean(rets > 0))
    sh = float(rets.mean()/rets.std()*np.sqrt(52)) if rets.std() > 0 else 0
    return {"annual_return": round(ann,4), "max_drawdown": round(float(dd),4),
            "sharpe": round(sh,4), "win_rate": round(wr,4),
            "total_return": round(float(nav-1),4), "n_periods": len(rets),
            "avg_period_ret": round(float(rets.mean()),5),
            "nav_curve": [{"date":c["date"],"nav":round(c["nav"],6)} for c in nav_curve]}


def run_master():
    import subprocess
    t0 = time.time()
    log("="*60)
    log(f"S023 真·星期几效应测试: 5个星期几并行, 持有{HOLD_DAYS}日, 不设止损")
    procs = []
    for wd in range(5):
        ck = f"{OUT_DIR}/wd_signals_{wd}.pkl"
        if os.path.exists(ck):
            log(f"[主控] {WD_NAME[wd]}已有缓存")
            continue
        p = subprocess.Popen(
            ["/Users/ziruzhu/stock-tools.old.20260725_204255/.venv/bin/python3",
             os.path.abspath(__file__), str(wd)])
        procs.append((wd, p))
        log(f"[主控] 启动{WD_NAME[wd]} PID={p.pid}")
    for wd, p in procs:
        p.wait()
        log(f"[主控] {WD_NAME[wd]} 退出 code={p.returncode}")

    result = {}
    log("\n=== 星期几效应结果 ===")
    log(f"{'星期':>6}{'年化':>10}{'回撤':>10}{'夏普':>8}{'胜率':>8}{'期数':>6}{'周均':>8}")
    for wd in range(5):
        ck = f"{OUT_DIR}/wd_signals_{wd}.pkl"
        if not os.path.exists(ck):
            log(f"{WD_NAME[wd]}: 无数据")
            continue
        sigs = pickle.load(open(ck, "rb"))
        m = compute_metrics(sigs)
        result[WD_NAME[wd]] = m
        log(f"{WD_NAME[wd]:>6}{m['annual_return']*100:>9.1f}%{m['max_drawdown']*100:>9.1f}%"
            f"{m['sharpe']:>8.2f}{m['win_rate']*100:>7.1f}%{m['n_periods']:>6}{m['avg_period_ret']*100:>7.2f}%")

    json.dump({"config":{"hold_days":HOLD_DAYS,"stop_loss":STOP_LOSS,"data_end":DATA_END,
                         "note":"真·固定星期几买入,持有5交易日,不设止损"},
               "by_weekday": result},
              open(OUT_FILE,"w"), ensure_ascii=False, indent=2, default=str)
    log(f"\n结果已写: {OUT_FILE}  耗时{(time.time()-t0)/60:.1f}分")


if __name__ == "__main__":
    if WORKER_WD is not None:
        run_worker(WORKER_WD)
    else:
        run_master()

