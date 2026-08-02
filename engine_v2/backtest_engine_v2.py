#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一回测引擎 v2 —— 修正7大框架级缺陷（2026-08-01）
================================================================
适用：S017/S019/S023/S024 等所有喜神池LightGBM选股策略

已修正的缺陷（相对v1）：
1. 持有天数：sell_idx = buy_idx + HOLD_DAYS - 1（含首尾共N天，之前多1天）
2. 标签口径：label = close(第N天) / open(第1天) - 1（与实际成交对齐，之前用close/close）
3. subsample_freq=1（让subsample=0.8生效，之前未设=0关闭了行采样）
4. min_child_samples=20（控制叶节点过拟合）
5. 止损：触及止损线用当日【最低价】成交（保守，之前用收盘价偏乐观）
6. 回撤：每日盯市净值计算（之前按调仓期，低估波动）
7. OOS封存期：config可设 OOS_HOLDOUT_START，只报该日期后的样本外指标

命令行：
  python3 backtest_engine_v2.py <config_name> [phase]
  例: python3 backtest_engine_v2.py s023        # 主控模式
      python3 backtest_engine_v2.py s023 2      # worker模式(只跑phase2)
"""
import json, sqlite3, time, os, sys, pickle, importlib.util
import numpy as np, pandas as pd
import lightgbm as lgb

# ============ 加载配置 ============
if len(sys.argv) < 2:
    print("用法: python3 backtest_engine_v2.py <config_name> [phase]")
    sys.exit(1)
CONFIG_NAME = sys.argv[1]
WORKER_PHASE = int(sys.argv[2]) if len(sys.argv) >= 3 else None

ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
cfg_path = os.path.join(ENGINE_DIR, f"config_{CONFIG_NAME}.py")
spec = importlib.util.spec_from_file_location("cfg", cfg_path)
cfg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cfg)

# 从config读取参数
HOLD_DAYS   = cfg.HOLD_DAYS         # 持有交易日数（含首尾）
REBAL_FREQ  = cfg.REBAL_FREQ        # 换仓频率（=phase数）
TOP_N       = cfg.TOP_N
TRAIN_MONTHS= cfg.TRAIN_MONTHS
STOP_LOSS_GRID = cfg.STOP_LOSS_GRID
PRICE_LIMIT = getattr(cfg, "PRICE_LIMIT", 500)
FEATURE_COLS= cfg.FEATURE_COLS
USE_TIME_FEATS = getattr(cfg, "USE_TIME_FEATS", False)
BACKTEST_START = cfg.BACKTEST_START
DATA_END    = cfg.DATA_END
OOS_HOLDOUT_START = getattr(cfg, "OOS_HOLDOUT_START", None)  # 封存期起始日
BC, SC      = cfg.BC, cfg.SC

WORK_DIR = cfg.WORK_DIR
PANEL_PATH = cfg.PANEL_PATH
DB_PATH = cfg.DB_PATH
XISHEN_PATH = cfg.XISHEN_PATH
VENV_PY = cfg.VENV_PY

N_PHASES = REBAL_FREQ
LGB_THREADS = max(1, (24 - 4) // max(N_PHASES, 1))

if WORKER_PHASE is None:
    LOG = f"{WORK_DIR}/{CONFIG_NAME}_run.log"
else:
    LOG = f"{WORK_DIR}/{CONFIG_NAME}_p{WORKER_PHASE}.log"
OUT_FILE = f"{WORK_DIR}/{CONFIG_NAME}_result.json"

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


def load_all_data():
    pool = pd.read_csv(XISHEN_PATH)
    xishen_set = set(pool["ts_code"])
    log(f"喜神池: {len(xishen_set)} 只")
    log("读取面板...")
    panel = pd.read_pickle(PANEL_PATH)
    log(f"重算label(未来{HOLD_DAYS}天，第1天开盘→第N天收盘)...")
    panel = panel.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    g = panel.groupby("ts_code", group_keys=False)
    close_sell = g["close_qfq"].transform(lambda s: s.shift(-HOLD_DAYS))
    open_buy = g["open_qfq"].transform(lambda s: s.shift(-1))
    panel["fwd_ret_fixed"] = close_sell / open_buy - 1
    # ★P0修正：先删除无效未来收益，再算中位数和标签（否则NaN变成False=0误导模型）
    panel = panel.dropna(subset=["fwd_ret_fixed"]).copy()
    med = panel.groupby("trade_date")["fwd_ret_fixed"].transform("median")
    panel["label"] = (panel["fwd_ret_fixed"] > med).astype("int8")
    if USE_TIME_FEATS:
        dt = pd.to_datetime(panel["trade_date"], format="%Y%m%d")
        panel["day_of_month"] = dt.dt.day.astype("float32")
        panel["month"] = dt.dt.month.astype("float32")
        panel["weekday"] = dt.dt.dayofweek.astype("float32")
    bl = load_blacklist()
    panel = panel[~panel["ts_code"].isin(bl)].reset_index(drop=True)
    panel = panel[~panel["ts_code"].str.endswith(".BJ")].reset_index(drop=True)
    panel = panel.dropna(subset=FEATURE_COLS, how="all")
    panel = panel[panel["trade_date"] <= DATA_END].reset_index(drop=True)
    log(f"面板清洗后(截至{DATA_END}): {len(panel):,} 行")
    all_trade_dates = sorted(panel["trade_date"].unique())
    all_trade_dates = [d for d in all_trade_dates if d >= BACKTEST_START]
    con = sqlite3.connect(DB_PATH)
    _rp = ",".join(f"'{d}'" for d in all_trade_dates)
    px = pd.read_sql(
        f"SELECT ts_code,trade_date,open,close,low FROM daily WHERE trade_date IN ({_rp})", con)
    con.close()
    for col in ["open", "close", "low"]:
        px[col] = pd.to_numeric(px[col], errors="coerce")
    px["trade_date"] = px["trade_date"].astype(str)
    open_lookup = panel.set_index(["ts_code", "trade_date"])["open_qfq"].sort_index()
    close_lookup = panel.set_index(["ts_code", "trade_date"])["close_qfq"].sort_index()
    px_i = px.set_index(["ts_code", "trade_date"])
    ratio = (px_i["low"] / px_i["close"]).reindex(
        pd.MultiIndex.from_arrays([panel["ts_code"], panel["trade_date"]]))
    panel["low_qfq"] = ratio.values * panel["close_qfq"].values
    low_lookup = panel.set_index(["ts_code", "trade_date"])["low_qfq"].sort_index()
    next_trade_date = {d: all_trade_dates[i+1]
                       for i, d in enumerate(all_trade_dates) if i+1 < len(all_trade_dates)}
    real_px_idx = px.set_index(["trade_date", "ts_code"])["close"]
    return (xishen_set, panel, all_trade_dates, open_lookup, close_lookup,
            low_lookup, next_trade_date, real_px_idx)


def build_signals_for_phase(phase, panel, xishen_set, all_trade_dates,
                             open_lookup, close_lookup, low_lookup,
                             next_trade_date, real_px_idx):
    ckpt = f"{WORK_DIR}/{CONFIG_NAME}_signals_phase{phase}.pkl"
    if os.path.exists(ckpt):
        log(f"[Phase{phase}] 已有缓存，跳过")
        return pickle.load(open(ckpt, "rb"))
    log(f"[Phase{phase}] 开始训练选股...")
    panel_by_date = {d: sub for d, sub in panel.groupby("trade_date")}
    score_dates = all_trade_dates[phase::N_PHASES]
    signals = []
    t_phase = time.time()
    for k, rd in enumerate(score_dates):
        rd_dt = pd.to_datetime(rd, format="%Y%m%d")
        train_start = (rd_dt - pd.DateOffset(months=TRAIN_MONTHS)).strftime("%Y%m%d")
        # ★P0修正：训练标签必须在打分日前已完全揭晓
        # 打分日rd收盘后打分，最新可用的已成熟样本是rd前HOLD_DAYS个交易日
        rd_idx = all_trade_dates.index(rd)
        if rd_idx < HOLD_DAYS:
            continue
        label_last_date = all_trade_dates[rd_idx - HOLD_DAYS]
        train_mask = (panel["trade_date"] >= train_start) & (panel["trade_date"] <= label_last_date)
        train_df = panel.loc[train_mask].dropna(subset=FEATURE_COLS + ["label"])
        if len(train_df) < 5000:
            continue
        score_df = panel_by_date.get(rd)
        if score_df is None or len(score_df) == 0:
            continue
        score_df = score_df[score_df["ts_code"].isin(xishen_set)].dropna(
            subset=FEATURE_COLS, how="all").copy()
        if PRICE_LIMIT < 99999:
            try:
                _keep = score_df["ts_code"].map(real_px_idx.loc[rd])
                score_df = score_df[(_keep <= PRICE_LIMIT) & (_keep.notna())]
            except (KeyError, TypeError):
                pass
        if len(score_df) < TOP_N:
            continue
        model = lgb.LGBMClassifier(
            boosting_type="gbdt", num_leaves=31, learning_rate=0.05,
            n_estimators=200, subsample=0.8, subsample_freq=1,
            colsample_bytree=0.8, min_child_samples=20,
            random_state=42, verbose=-1, n_jobs=LGB_THREADS)
        model.fit(train_df[FEATURE_COLS], train_df["label"])
        score_df["score"] = model.predict_proba(score_df[FEATURE_COLS])[:, 1]
        top20 = score_df.sort_values("score", ascending=False).head(TOP_N)["ts_code"].tolist()
        buy_date = next_trade_date.get(rd)
        if buy_date is None:
            continue
        try:
            buy_idx = all_trade_dates.index(buy_date)
            sell_idx = buy_idx + HOLD_DAYS - 1
            if sell_idx >= len(all_trade_dates):
                continue
            sell_date = all_trade_dates[sell_idx]
        except (ValueError, IndexError):
            continue
        hold_dates = all_trade_dates[buy_idx:sell_idx + 1]
        stocks = []
        for code in top20:
            try:
                entry_px = open_lookup.loc[(code, buy_date)]
                if pd.isna(entry_px) or entry_px <= 0:
                    continue
                daily_close, daily_low = [], []
                for hd in hold_dates:
                    try:
                        cl = close_lookup.loc[(code, hd)]
                        lw = low_lookup.loc[(code, hd)]
                        daily_close.append(float(cl) if (not pd.isna(cl) and cl > 0) else None)
                        daily_low.append(float(lw) if (not pd.isna(lw) and lw > 0) else None)
                    except KeyError:
                        daily_close.append(None)
                        daily_low.append(None)
                stocks.append({"code": code, "entry_px": float(entry_px),
                               "daily_close": daily_close, "daily_low": daily_low})
            except KeyError:
                continue
        if stocks:
            signals.append({"score_date": rd, "buy_date": buy_date,
                            "sell_date": sell_date, "stocks": stocks})
        if (k + 1) % 30 == 0:
            log(f"  [Phase{phase}] {k+1}/{len(score_dates)}期, 耗时{(time.time()-t_phase)/60:.1f}分")
    pickle.dump(signals, open(ckpt, "wb"))
    log(f"[Phase{phase}] 完成 {len(signals)}期，耗时{(time.time()-t_phase)/60:.1f}分")
    return signals


def simulate(signals, stop_loss, track_daily_nav=True):
    """阶段B：模拟出场。★修正5：止损用当日最低价；★修正6：每日盯市回撤"""
    nav = 1.0
    period_rets = []
    prev_holdings = set()
    total_stops, total_checks = 0, 0
    daily_nav_list = []
    nav_curve = []

    for sig in signals:
        rets, valid = [], []
        stop_count = 0
        per_stock_daily = []
        for st in sig["stocks"]:
            entry = st["entry_px"]
            dc = st["daily_close"]
            dl = st["daily_low"]
            stop_hit, exit_r = False, None
            daily_r = []
            for j in range(len(dc)):
                lw = dl[j]
                cl = dc[j]
                if stop_loss is not None and lw is not None and (lw / entry - 1) <= stop_loss:
                    # ★P1修正：按当日最低价成交（保守），而非止损线价格
                    exit_r = lw / entry - 1
                    stop_hit = True
                    while len(daily_r) < len(dc):
                        daily_r.append(exit_r)
                    break
                daily_r.append((cl / entry - 1) if cl is not None else (daily_r[-1] if daily_r else 0.0))
            if stop_hit:
                stop_count += 1
            else:
                last_cl = next((c for c in reversed(dc) if c is not None), None)
                if last_cl is None:
                    continue
                exit_r = last_cl / entry - 1
            rets.append(exit_r)
            valid.append(st["code"])
            per_stock_daily.append(daily_r)
        total_stops += stop_count
        total_checks += len(valid)
        if not rets:
            continue
        gross = float(np.mean(rets))
        curr = set(valid)
        # prev_holdings仅保留用于检测实际发生变化（如需扩展换手率统计）

        # ★P1修正：每日净值纳入成本，与期末nav连续（v2.2已修正）
        if track_daily_nav and per_stock_daily:
            maxlen = max(len(d) for d in per_stock_daily)
            # 买入当天先扣买入成本
            nav_in_period = nav * (1 - BC)
            for day_j in range(maxlen):
                day_rs = [d[day_j] if day_j < len(d) else d[-1] for d in per_stock_daily]
                port_r = float(np.mean(day_rs))
                # 最后一天同时包含毛收益+卖出成本
                if day_j == maxlen - 1:
                    day_nav = nav_in_period * (1 + port_r) * (1 - SC)
                else:
                    day_nav = nav_in_period * (1 + port_r)
                daily_nav_list.append(day_nav)
        
        # 期末净值 = 期初 * (1-买入成本) * (1+毛收益) * (1-卖出成本)
        nav = nav * (1 - BC) * (1 + gross) * (1 - SC)
        # 期收益 = (期末/期初 - 1)
        net = (1 - BC) * (1 + gross) * (1 - SC) - 1
        period_rets.append(net)
        nav_curve.append({"date": sig["sell_date"], "buy_date": sig["buy_date"], "nav": round(nav, 6)})
        prev_holdings = curr

    if not period_rets:
        return None
    pr = np.array(period_rets)
    if daily_nav_list:
        arr = np.array([1.0] + daily_nav_list)
    else:
        arr = np.array([1.0] + [c["nav"] for c in nav_curve])
    dd = (arr / np.maximum.accumulate(arr) - 1).min()
    n_years = len(period_rets) * HOLD_DAYS / 252.0
    ann = nav ** (1 / n_years) - 1 if n_years > 0 and nav > 0 else 0
    sh = float(pr.mean() / pr.std() * np.sqrt(252 / HOLD_DAYS)) if pr.std() > 0 else 0
    
    result = {
        "annual_return": round(ann, 4), "max_drawdown": round(float(dd), 4),
        "sharpe": round(sh, 4), "win_rate": round(float(np.mean(pr > 0)), 4),
        "total_return": round(float(nav - 1), 4), "n_periods": len(period_rets),
        "total_stops": total_stops, "total_checks": total_checks,
        "nav_curve": nav_curve
    }
    
    if OOS_HOLDOUT_START:
        # ★P1修正：按buy_date筛选（nav_curve现在记录了buy_date）
        # 避免OOS前买入、OOS后卖出的跨期收益被多算进封存期
        oos_curve = [x for x in nav_curve if x.get("buy_date", x["date"]) >= OOS_HOLDOUT_START]
        if len(oos_curve) >= 2:
            oos_rets = []
            # ★P1修正：以封存期起点前最后一个nav为基准（而非封存期第一条nav）
            # 找OOS起点前最后一条nav作为初始基准
            pre_oos = [x for x in nav_curve if x["date"] < OOS_HOLDOUT_START]
            oos_base_nav = pre_oos[-1]["nav"] if pre_oos else 1.0
            prev_nav = oos_base_nav
            for i, x in enumerate(oos_curve):
                r = x["nav"] / prev_nav - 1
                oos_rets.append(r)
                prev_nav = x["nav"]
            oos_nav = oos_curve[-1]["nav"]
            if oos_rets:
                oos_pr = np.array(oos_rets)
                oos_years = len(oos_rets) * HOLD_DAYS / 252.0
                # ★P1修正：分母应用oos_base_nav，否则首期收益进了rets但不在CAGR
                oos_ann = (oos_nav / oos_base_nav) ** (1/oos_years) - 1 if oos_years > 0 else 0
                # ★P1修正：以oos_base_nav为起点，包含首期路径的回撤
                oos_arr = np.array([oos_base_nav] + [c["nav"] for c in oos_curve])
                oos_dd = (oos_arr / np.maximum.accumulate(oos_arr) - 1).min()
                oos_sh = oos_pr.mean() / oos_pr.std() * np.sqrt(252/HOLD_DAYS) if oos_pr.std()>0 else 0
                result["oos_annual_return"] = round(oos_ann, 4)
                result["oos_max_drawdown"] = round(float(oos_dd), 4)
                result["oos_sharpe"] = round(float(oos_sh), 4)
                result["oos_n_periods"] = len(oos_rets)
    
    return result


def run_worker():
    t0 = time.time()
    log("="*60)
    log(f"[Worker] {CONFIG_NAME} Phase{WORKER_PHASE} 启动")
    data = load_all_data()
    xishen_set, panel, all_trade_dates, open_lookup, close_lookup, low_lookup, next_trade_date, real_px_idx = data
    build_signals_for_phase(WORKER_PHASE, panel, xishen_set, all_trade_dates,
                             open_lookup, close_lookup, low_lookup, next_trade_date, real_px_idx)
    log(f"[Worker] Phase{WORKER_PHASE} 完成，耗时{(time.time()-t0)/60:.1f}分")


def run_master():
    t0 = time.time()
    log("="*70)
    log(f"统一引擎v2 —— {CONFIG_NAME} ({HOLD_DAYS}天版)")
    log("="*70)
    log(f"已修正：持有天数、标签口径、subsample_freq、止损最低价、每日回撤")
    log(f"配置：持有{HOLD_DAYS}天(含首尾), {N_PHASES}phase, Top{TOP_N}, 训练{TRAIN_MONTHS}月")
    # ★批次并行：每次4个phase并发（平衡内存和CPU）
    if N_PHASES > 1:
        import subprocess
        BATCH_SIZE = 4  # 同时跑4个phase worker
        log(f"批次并行启动{N_PHASES}个phase (每批{BATCH_SIZE}个)...")
        for batch_start in range(0, N_PHASES, BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, N_PHASES)
            batch_phases = range(batch_start, batch_end)
            log(f"  启动批次 phase{batch_start}-{batch_end-1}...")
            procs = []
            for p in batch_phases:
                cmd = [VENV_PY, __file__, CONFIG_NAME, str(p)]
                logf = f"{WORK_DIR}/{CONFIG_NAME}_p{p}.log"
                fh = open(logf, "w")
                proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT)
                procs.append((proc, fh, p))
            for proc, fh, p in procs:
                proc.wait()
                fh.close()
                log(f"  Phase{p}完成(exit={proc.returncode})")
        log(f"全部{N_PHASES}个phase完成")
    else:
        # 单phase直接跑
        data = load_all_data()
        xishen_set, panel, all_trade_dates, open_lookup, close_lookup, low_lookup, next_trade_date, real_px_idx = data
        build_signals_for_phase(0, panel, xishen_set, all_trade_dates,
                                 open_lookup, close_lookup, low_lookup, next_trade_date, real_px_idx)
    # 加载所有phase信号
    log("加载信号并模拟...")
    all_sigs = []
    for p in range(N_PHASES):
        ckpt = f"{WORK_DIR}/{CONFIG_NAME}_signals_phase{p}.pkl"
        if os.path.exists(ckpt):
            all_sigs.append((p, pickle.load(open(ckpt, "rb"))))
    if not all_sigs:
        log("无有效信号，退出")
        return
    # 遍历止损档位
    results = {"config": CONFIG_NAME, "hold_days": HOLD_DAYS, "n_phases": N_PHASES,
               "single_strategies": [], "ensemble": []}
    for phase, sigs in all_sigs:
        for sl in STOP_LOSS_GRID:
            log(f"  Phase{phase} SL={sl}...")
            r = simulate(sigs, sl)
            if r:
                r["phase"] = phase
                r["stop_loss"] = sl
                results["single_strategies"].append(r)
    # 组合：各phase独立模拟后等权平均，避免跨phase混合prev_holdings导致成本计算错误
    for sl in STOP_LOSS_GRID:
        phase_results = []
        for _, sigs in all_sigs:
            r = simulate(sigs, sl, track_daily_nav=False)
            if r:
                phase_results.append(r)
        if phase_results:
            # 等权平均各项指标
            avg_ann = float(np.mean([x["annual_return"] for x in phase_results]))
            avg_dd  = float(np.min([x["max_drawdown"] for x in phase_results]))  # 取最大回撤
            avg_sh  = float(np.mean([x["sharpe"] for x in phase_results]))
            avg_wr  = float(np.mean([x["win_rate"] for x in phase_results]))
            avg_tr  = float(np.mean([x["total_return"] for x in phase_results]))
            avg_np  = int(np.mean([x["n_periods"] for x in phase_results]))
            ens = {"annual_return": round(avg_ann,4), "max_drawdown": round(avg_dd,4),
                   "sharpe": round(avg_sh,4), "win_rate": round(avg_wr,4),
                   "total_return": round(avg_tr,4), "n_periods": avg_np,
                   "stop_loss": sl, "n_phases_combined": len(phase_results)}
            if OOS_HOLDOUT_START and "oos_annual_return" in phase_results[0]:
                ens["oos_annual_return"] = round(float(np.mean([x.get("oos_annual_return",0) for x in phase_results])),4)
                ens["oos_max_drawdown"]  = round(float(np.min([x.get("oos_max_drawdown",0) for x in phase_results])),4)
                ens["oos_sharpe"]        = round(float(np.mean([x.get("oos_sharpe",0) for x in phase_results])),4)
            results["ensemble"].append(ens)
    json.dump(results, open(OUT_FILE, "w"), indent=2, ensure_ascii=False)
    log(f"结果已写入: {OUT_FILE}")
    # 打印摘要
    log("\n" + "="*70)
    log(f"单策略({N_PHASES}个phase × {len(STOP_LOSS_GRID)}档止损):")
    for s in results["single_strategies"]:
        log(f"  Phase{s['phase']} SL={s['stop_loss']}: 年化{s['annual_return']*100:.1f}% 回撤{s['max_drawdown']*100:.1f}% 夏普{s['sharpe']:.2f}")
    log(f"\n组合策略({N_PHASES}phase合并):")
    for s in results["ensemble"]:
        log(f"  [{N_PHASES}phase SL={s['stop_loss']}]: 年化{s['annual_return']*100:.1f}% 回撤{s['max_drawdown']*100:.1f}% 夏普{s['sharpe']:.2f}")
    log(f"\n总耗时{(time.time()-t0)/60:.1f}分钟")
    log("="*70)


if __name__ == "__main__":
    if WORKER_PHASE is not None:
        run_worker()
    else:
        run_master()
