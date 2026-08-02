#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S023 网格搜索 Multi（参数化版：支持1/2/3/5天持有）
===============================================================================
命令行：python3 train_grid_multi.py <持有天数>   例: python3 train_grid_multi.py 3

特性：
  - HOLD_DAYS、HORIZON(label窗口)、N_PHASES(分批相位数=持有天数) 随参数自动配置
  - signals缓存和结果文件名带天数后缀，避免多版本互相覆盖
  - 训练与出场解耦（V2架构）：每phase训练一次，6种止损在缓存上纯循环模拟
  - 相位数×每相位期数≈总交易日恒定，故各版本训练量相近(约2270次)，每版本约3.5小时
  - ⚠️持有越短交易成本越高：5天版年成本约10%，3天约17%，2天约25%，1天约50%
"""
import json, sqlite3, time, os, sys, pickle
import numpy as np, pandas as pd
import lightgbm as lgb

if len(sys.argv) < 2:
    print("用法: python3 train_grid_parallel.py <持有天数> [phase]")
    print("  无phase参数 → 主控模式：并行启动所有phase，再做阶段B合并")
    print("  有phase参数 → worker模式：只跑该phase的阶段A存pkl")
    sys.exit(1)
HOLD_DAYS = int(sys.argv[1])
if HOLD_DAYS < 1:
    print(f"错误：持有天数{HOLD_DAYS}非法")
    sys.exit(1)
WORKER_PHASE = int(sys.argv[2]) if len(sys.argv) >= 3 else None  # None=主控模式

WORK_DIR = "/Users/ziruzhu/stock-tools/_s024_run"
BASE_DIR = WORK_DIR
OUT_DIR  = WORK_DIR
PANEL_PATH = f"{WORK_DIR}/features_panel.pkl"
DB_PATH = "/Users/ziruzhu/stock-tools/_weekday_4d_run/stock_mini.db"
XISHEN_PATH = f"{WORK_DIR}/xishen_plus_pool.csv"

HORIZON = HOLD_DAYS
N_PHASES = HOLD_DAYS
BACKTEST_START = "20170101"
DATA_END = "20260717"
TRAIN_MONTHS = 12
TOP_N = 20
PRICE_LIMIT = 500
BC, SC = 0.00025, 0.00125
STOP_LOSS_GRID = [-0.06, -0.08, -0.10, -0.12, -0.15, None]

# ★并行关键：每个worker限制LightGBM线程数，避免多进程互相抢核
# 24核 / N_PHASES个worker，留4核给系统
LGB_THREADS = max(1, (24 - 4) // N_PHASES)

if WORKER_PHASE is None:
    LOG  = f"{OUT_DIR}/grid_{HOLD_DAYS}d_run.log"
else:
    LOG  = f"{OUT_DIR}/grid_{HOLD_DAYS}d_p{WORKER_PHASE}.log"
OUT_FILE = f"{OUT_DIR}/s024_grid_{HOLD_DAYS}d_result.json"

FEATURE_COLS = [
    "mom_5","mom_10","mom_20","mom_60","mom_120",
    "turnover_rate","turnover_rate_f","volume_ratio","vol_chg_20",
    "bias_5","bias_10","bias_20","bias_60",
    "macd_dif","macd_dea","macd","kdj_k","kdj_d","kdj_j",
    "rsi_6","rsi_12","rsi_24","cci","boll_pct","boll_width",
    "pe","pe_ttm","pb","ps","ps_ttm","dv_ttm",
    "net_mf_ratio","lg_buy_ratio",
    # S024新增：时间特征(月内日期/月份/星期)
    "day_of_month","month","weekday",
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

def build_signals_for_phase(phase, panel, xishen_set, all_trade_dates,
                            open_lookup, close_lookup, next_trade_date, real_px_idx):
    """阶段A：训练+选股，缓存每期20只的entry价和持有期逐日收盘价序列（不含止损逻辑）"""
    ckpt = f"{OUT_DIR}/signals_{HOLD_DAYS}d_phase{phase}.pkl"
    if os.path.exists(ckpt):
        log(f"[Phase{phase}] 已有缓存，跳过训练")
        return pickle.load(open(ckpt, "rb"))

    log(f"[Phase{phase}] 开始训练选股...")
    panel_by_date = {d: sub for d, sub in panel.groupby("trade_date")}
    # 该phase的打分日：从第phase个交易日起，每HOLD_DAYS取一个
    score_dates = all_trade_dates[phase::HOLD_DAYS]
    signals = []
    t_phase = time.time()

    for k, rd in enumerate(score_dates):
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
        if PRICE_LIMIT < 99999:
            try:
                _day_px = real_px_idx.loc[rd]
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
        top20 = score_df.sort_values("score", ascending=False).head(TOP_N)["ts_code"].tolist()

        buy_date = next_trade_date.get(rd)
        if buy_date is None:
            continue
        try:
            buy_idx = all_trade_dates.index(buy_date)
            sell_idx = buy_idx + HOLD_DAYS
            if sell_idx >= len(all_trade_dates):
                continue
            sell_date = all_trade_dates[sell_idx]
        except (ValueError, IndexError):
            continue

        # 缓存每只票的entry开盘价 + 持有期(buy次日~sell)逐日收盘价
        hold_dates = all_trade_dates[buy_idx+1:sell_idx+1]  # 买入后到卖出日的收盘检查点
        stocks = []
        for code in top20:
            try:
                entry_px = open_lookup.loc[(code, buy_date)]
                if pd.isna(entry_px) or entry_px <= 0:
                    continue
                daily_close = []
                for hd in hold_dates:
                    try:
                        cl = close_lookup.loc[(code, hd)]
                        daily_close.append(float(cl) if (not pd.isna(cl) and cl > 0) else None)
                    except KeyError:
                        daily_close.append(None)
                # 卖出日收盘价（daily_close最后一个就是sell_date收盘）
                stocks.append({"code": code, "entry_px": float(entry_px), "daily_close": daily_close})
            except KeyError:
                continue
        if stocks:
            signals.append({"score_date": rd, "buy_date": buy_date, "sell_date": sell_date, "stocks": stocks})

        if (k+1) % 30 == 0:
            log(f"  [Phase{phase}] {k+1}/{len(score_dates)}期, 耗时{(time.time()-t_phase)/60:.1f}分")

    pickle.dump(signals, open(ckpt, "wb"))
    log(f"[Phase{phase}] 训练完成 {len(signals)}期，耗时{(time.time()-t_phase)/60:.1f}分，已缓存")
    return signals


def simulate(signals, stop_loss):
    """阶段B：给定止损线，在缓存信号上模拟出场，返回nav_curve和trades（纯循环，极快）"""
    nav = 1.0
    nav_curve, period_rets = [], []
    prev_holdings = set()
    total_stops, total_checks = 0, 0

    for sig in signals:
        rets, valid = [], []
        stop_count = 0
        for st in sig["stocks"]:
            entry = st["entry_px"]
            dc = st["daily_close"]
            stop_hit, exit_r = False, None
            # 逐日检查止损（daily_close = 买入后每天的收盘，最后一个是sell_date）
            for j, cl in enumerate(dc):
                if cl is None:
                    continue
                r_j = cl/entry - 1
                if stop_loss is not None and r_j <= stop_loss:
                    exit_r = r_j; stop_hit = True; break
            if stop_hit:
                stop_count += 1
            else:
                # 用最后一个非None收盘价作为卖出价
                last_cl = next((c for c in reversed(dc) if c is not None), None)
                if last_cl is None:
                    continue
                exit_r = last_cl/entry - 1
            rets.append(exit_r); valid.append(st["code"])
        total_stops += stop_count; total_checks += len(valid)
        if not rets:
            continue
        gross = float(np.mean(rets))
        curr = set(valid)
        bought = curr - prev_holdings; sold = prev_holdings - curr
        nc = len(curr) if curr else 1; np_ = len(prev_holdings) if prev_holdings else 1
        cost = (len(bought)/nc)*BC + (len(sold)/np_ if np_>0 else 0)*(SC+0.0005)
        net = gross - cost
        nav *= (1+net)
        nav_curve.append({"date": sig["sell_date"], "nav": round(nav, 6)})
        period_rets.append(net)
        prev_holdings = curr

    if not period_rets:
        return None
    pr = np.array(period_rets)
    dates = [c["date"] for c in nav_curve]
    n_years = (pd.to_datetime(dates[-1]) - pd.to_datetime(dates[0])).days/365.25
    arr = np.array([1.0]+[c["nav"] for c in nav_curve])
    dd = (arr/np.maximum.accumulate(arr)-1).min()
    ann = nav**(1/n_years)-1 if n_years>0 and nav>0 else 0
    sh = float(pr.mean()/pr.std()*np.sqrt(252/HOLD_DAYS)) if pr.std()>0 else 0
    return {
        "annual_return": round(ann,4), "max_drawdown": round(float(dd),4),
        "sharpe": round(sh,4), "win_rate": round(float(np.mean(pr>0)),4),
        "total_return": round(float(nav-1),4), "n_periods": len(period_rets),
        "total_stops": total_stops, "total_checks": total_checks,
        "nav_curve": nav_curve
    }


def load_all_data():
    """加载面板+算label+清洗+建索引，worker和主控共用"""
    pool = pd.read_csv(XISHEN_PATH)
    xishen_set = set(pool["ts_code"])
    log(f"喜神池: {len(xishen_set)} 只")
    log("读取面板...")
    panel = pd.read_pickle(PANEL_PATH)
    log(f"重算label(未来{HORIZON}天)...")
    panel = panel.sort_values(["ts_code","trade_date"]).reset_index(drop=True)
    g = panel.groupby("ts_code", group_keys=False)
    panel["fwd_ret"] = g["close_qfq"].transform(lambda s: s.shift(-HORIZON)/s-1)
    med = panel.groupby("trade_date")["fwd_ret"].transform("median")
    panel["label"] = (panel["fwd_ret"] > med).astype("Int8")
    panel = panel.dropna(subset=["label"])
    bl = load_blacklist()
    panel = panel[~panel["ts_code"].isin(bl)].reset_index(drop=True)
    panel = panel[~panel["ts_code"].str.endswith(".BJ")].reset_index(drop=True)
    # S024新增：生成时间特征
    _dt = pd.to_datetime(panel["trade_date"], format="%Y%m%d")
    panel["day_of_month"] = _dt.dt.day.astype("float32")
    panel["month"] = _dt.dt.month.astype("float32")
    panel["weekday"] = _dt.dt.dayofweek.astype("float32")
    panel = panel.dropna(subset=FEATURE_COLS, how="all")
    panel = panel[panel["trade_date"] <= DATA_END].reset_index(drop=True)
    log(f"面板清洗后(截至{DATA_END}): {len(panel):,} 行")

    all_trade_dates = sorted(panel["trade_date"].unique())
    all_trade_dates = [d for d in all_trade_dates if d >= BACKTEST_START]
    _con = sqlite3.connect(DB_PATH)
    _rp = ",".join(f"'{d}'" for d in all_trade_dates)
    _rpx = pd.read_sql(f"SELECT ts_code,trade_date,close FROM daily WHERE trade_date IN ({_rp})", _con)
    _con.close()
    _rpx["close"] = pd.to_numeric(_rpx["close"], errors="coerce")
    _rpx["trade_date"] = _rpx["trade_date"].astype(str)
    real_px_idx = _rpx.set_index(["trade_date","ts_code"])["close"]
    open_lookup = panel.set_index(["ts_code","trade_date"])["open_qfq"].sort_index()
    close_lookup = panel.set_index(["ts_code","trade_date"])["close_qfq"].sort_index()
    next_trade_date = {d: all_trade_dates[i+1] for i,d in enumerate(all_trade_dates) if i+1<len(all_trade_dates)}
    return (xishen_set, panel, all_trade_dates, open_lookup, close_lookup, next_trade_date, real_px_idx)


def run_worker():
    """worker模式：只跑单个phase的阶段A，存pkl后退出"""
    t0 = time.time()
    log("="*60)
    log(f"[Worker] {HOLD_DAYS}天版 Phase{WORKER_PHASE} 启动 (LGB线程={LGB_THREADS})")
    data = load_all_data()
    xishen_set, panel, all_trade_dates, open_lookup, close_lookup, next_trade_date, real_px_idx = data
    build_signals_for_phase(WORKER_PHASE, panel, xishen_set, all_trade_dates,
                            open_lookup, close_lookup, next_trade_date, real_px_idx)
    log(f"[Worker] Phase{WORKER_PHASE} 完成，耗时{(time.time()-t0)/60:.1f}分")


def run_master():
    """主控模式：并行启动N个worker跑阶段A，全部完成后跑阶段B合并"""
    import subprocess
    t0 = time.time()
    log("="*60)
    log(f"S023网格并行: {HOLD_DAYS}天持有 {N_PHASES}phase并行 × {len(STOP_LOSS_GRID)}止损 (每worker {LGB_THREADS}线程)")

    # 并行启动N_PHASES个worker
    procs = []
    for phase in range(N_PHASES):
        ck = f"{OUT_DIR}/signals_{HOLD_DAYS}d_phase{phase}.pkl"
        if os.path.exists(ck):
            log(f"[主控] Phase{phase}已有缓存，跳过")
            continue
        p = subprocess.Popen(
            ["/Users/ziruzhu/stock-tools.old.20260725_204255/.venv/bin/python3",
             os.path.abspath(__file__), str(HOLD_DAYS), str(phase)])
        procs.append((phase, p))
        log(f"[主控] 启动Phase{phase} worker PID={p.pid}")
    # 等全部完成
    for phase, p in procs:
        p.wait()
        log(f"[主控] Phase{phase} worker退出(code={p.returncode})")

    # 加载各phase的信号缓存
    all_signals = {}
    for phase in range(N_PHASES):
        ck = f"{OUT_DIR}/signals_{HOLD_DAYS}d_phase{phase}.pkl"
        if not os.path.exists(ck):
            log(f"[主控] ✗ Phase{phase}缺失pkl，跳过")
            continue
        all_signals[phase] = pickle.load(open(ck, "rb"))
    if not all_signals:
        log("[主控] ✗ 无任何phase完成，退出")
        return

    # 阶段B：模拟single(phase×stop) + combined
    log("\n阶段B: 模拟出场（快）...")
    singles, combined = [], []
    for phase in sorted(all_signals.keys()):
        for sl in STOP_LOSS_GRID:
            res = simulate(all_signals[phase], sl)
            if res:
                res["phase"] = phase; res["stop_loss"] = sl
                singles.append(res)
                sl_tag = f"{sl*100:.0f}%" if sl is not None else "None"
                log(f"  P{phase} SL={sl_tag}: 年化{res['annual_return']*100:.1f}% 回撤{res['max_drawdown']*100:.1f}% 夏普{res['sharpe']:.2f}")

    # combined: 每种止损线，5个phase各投1/5资金的等权组合
    # ★正确做法：各phase的nav曲线按全体日期并集"前向填充"对齐（无新卖出日沿用上一nav，
    #   代表持仓市值延续），再对5条对齐后的曲线求均值。否则不同phase卖出日不重叠会导致
    #   单点均值跳变、算出虚假巨大回撤（V1旧bug：-73%假回撤）。
    n_done = len(all_signals)
    for sl in STOP_LOSS_GRID:
        subs = [s for s in singles if s["stop_loss"]==sl]
        if len(subs) < n_done:
            continue
        # 全体日期并集
        all_d = sorted(set(c["date"] for s in subs for c in s["nav_curve"]))
        # 每个phase前向填充到全体日期
        aligned = []  # 每个phase一条对齐后的nav数组
        for s in subs:
            cd = {c["date"]: c["nav"] for c in s["nav_curve"]}
            seq, last = [], 1.0
            for d in all_d:
                if d in cd:
                    last = cd[d]
                seq.append(last)
            aligned.append(seq)
        aligned = np.array(aligned)          # shape=(n_phase, n_dates)
        comb = aligned.mean(axis=0)          # 等权组合nav
        cnav = [{"date": d, "nav": float(v)} for d, v in zip(all_d, comb)]
        n_years = (pd.to_datetime(all_d[-1])-pd.to_datetime(all_d[0])).days/365.25
        ann = comb[-1]**(1/n_years)-1 if n_years>0 and comb[-1]>0 else 0
        arr = np.concatenate([[1.0], comb])
        dd = (arr/np.maximum.accumulate(arr)-1).min()
        dret = np.diff(arr)/arr[:-1]
        sh = float(dret.mean()/dret.std()*np.sqrt(252/HOLD_DAYS)) if dret.std()>0 else 0
        sl_tag = f"{sl*100:.0f}%" if sl is not None else "None"
        combined.append({"type":f"combined_{n_done}phase","stop_loss":sl,
                         "annual_return":round(ann,4),"max_drawdown":round(float(dd),4),
                         "sharpe":round(sh,4),"total_return":round(float(comb[-1]-1),4),
                         "nav_curve":cnav})
        log(f"  [组合{n_done}phase SL={sl_tag}]: 年化{ann*100:.1f}% 回撤{dd*100:.1f}% 夏普{sh:.2f}")

    json.dump({"config":{"horizon":HORIZON,"hold_days":HOLD_DAYS,"data_end":DATA_END,
                         "n_phases":N_PHASES,"stop_loss_grid":STOP_LOSS_GRID,
                         "parallel":True,"lgb_threads_per_worker":LGB_THREADS,
                         "note":"已剔除20260720污染周,数据截至0717"},
               "single_strategies":singles,"combined_strategies":combined},
              open(OUT_FILE,"w"),ensure_ascii=False,indent=2,default=str)
    log(f"\n结果已写出: {OUT_FILE}  总耗时{(time.time()-t0)/60:.1f}分钟")


if __name__ == "__main__":
    if WORKER_PHASE is not None:
        run_worker()   # worker模式：只跑单个phase
    else:
        run_master()   # 主控模式：并行拉起所有worker + 阶段B合并
