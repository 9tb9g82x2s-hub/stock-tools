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
    print("用法: python3 train_grid_multi.py <持有天数(1/2/3/5)>")
    sys.exit(1)
HOLD_DAYS = int(sys.argv[1])
if HOLD_DAYS not in [1, 2, 3, 5]:
    print(f"错误：持有天数{HOLD_DAYS}不在支持范围[1,2,3,5]")
    sys.exit(1)

BASE_DIR = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S013-喜神池LightGBM选股"
OUT_DIR  = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-26-S023-周度5天换仓"
PANEL_PATH = f"{BASE_DIR}/features_panel.pkl"
DB_PATH = "/Users/ziruzhu/stock-data/stock_all.db"
XISHEN_PATH = f"{BASE_DIR}/xishen_plus_pool.csv"

HORIZON = HOLD_DAYS       # label窗口=持有天数
N_PHASES = HOLD_DAYS      # 分批相位数=持有天数（1天版只有1相位=不分批）
BACKTEST_START = "20170101"
DATA_END = "20260717"     # ★剔除20260720~24前复权污染周
TRAIN_MONTHS = 12
TOP_N = 20
PRICE_LIMIT = 500
BC, SC = 0.00025, 0.00125
STOP_LOSS_GRID = [-0.06, -0.08, -0.10, -0.12, -0.15, None]

LOG  = f"{OUT_DIR}/grid_{HOLD_DAYS}d_run.log"
OUT_FILE = f"{OUT_DIR}/s023_grid_{HOLD_DAYS}d_result.json"

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
            random_state=42, verbose=-1)
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


def main():
    t0 = time.time()
    log("="*60)
    log(f"S023 网格Multi: {HOLD_DAYS}天持有 {N_PHASES}phase × {len(STOP_LOSS_GRID)}止损, 数据截至{DATA_END}")

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
    panel = panel.dropna(subset=FEATURE_COLS, how="all")
    # ★剔除污染周：数据截至DATA_END
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

    # 阶段A：5个phase各训练一次
    all_signals = {}
    for phase in range(N_PHASES):
        all_signals[phase] = build_signals_for_phase(
            phase, panel, xishen_set, all_trade_dates,
            open_lookup, close_lookup, next_trade_date, real_px_idx)

    # 阶段B：模拟30个single(phase×stop) + 6个combined
    log("\n阶段B: 模拟出场（快）...")
    singles, combined = [], []
    for phase in range(N_PHASES):
        for sl in STOP_LOSS_GRID:
            res = simulate(all_signals[phase], sl)
            if res:
                res["phase"] = phase; res["stop_loss"] = sl
                singles.append(res)
                sl_tag = f"{sl*100:.0f}%" if sl is not None else "None"
                log(f"  P{phase} SL={sl_tag}: 年化{res['annual_return']*100:.1f}% 回撤{res['max_drawdown']*100:.1f}% 夏普{res['sharpe']:.2f}")

    # combined: 每种止损线，把5个phase的nav按日期等权合并
    for sl in STOP_LOSS_GRID:
        subs = [s for s in singles if s["stop_loss"]==sl]
        if len(subs) < N_PHASES:
            continue
        date2navs = {}
        for s in subs:
            for c in s["nav_curve"]:
                date2navs.setdefault(c["date"], []).append(c["nav"])
        dates = sorted(date2navs.keys())
        cnav = [{"date": d, "nav": float(np.mean(date2navs[d]))} for d in dates]
        navs = np.array([c["nav"] for c in cnav])
        n_years = (pd.to_datetime(dates[-1])-pd.to_datetime(dates[0])).days/365.25
        ann = navs[-1]**(1/n_years)-1 if n_years>0 and navs[-1]>0 else 0
        arr = np.array([1.0]+list(navs))
        dd = (arr/np.maximum.accumulate(arr)-1).min()
        # combined的夏普/胜率用日度nav收益近似
        dret = np.diff(np.array([1.0]+list(navs)))/np.array([1.0]+list(navs[:-1]))
        sh = float(dret.mean()/dret.std()*np.sqrt(252/HOLD_DAYS)) if dret.std()>0 else 0
        sl_tag = f"{sl*100:.0f}%" if sl is not None else "None"
        combined.append({"type":"combined_5phase","stop_loss":sl,
                         "annual_return":round(ann,4),"max_drawdown":round(float(dd),4),
                         "sharpe":round(sh,4),"total_return":round(float(navs[-1]-1),4),
                         "nav_curve":cnav})
        log(f"  [组合5phase SL={sl_tag}]: 年化{ann*100:.1f}% 回撤{dd*100:.1f}% 夏普{sh:.2f}")

    json.dump({"config":{"horizon":HORIZON,"hold_days":HOLD_DAYS,"data_end":DATA_END,
                         "n_phases":N_PHASES,"stop_loss_grid":STOP_LOSS_GRID,
                         "note":"已剔除20260720污染周,数据截至0717"},
               "single_strategies":singles,"combined_strategies":combined},
              open(OUT_FILE,"w"),ensure_ascii=False,indent=2,default=str)
    log(f"\n结果已写出: {OUT_FILE}  总耗时{(time.time()-t0)/60:.1f}分钟")

if __name__ == "__main__":
    main()

