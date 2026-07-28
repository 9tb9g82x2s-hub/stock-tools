#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S023变体：5天滚动持有 + 分批错开轮动(staggered) + 止损敏感性网格搜索
===============================================================================
目标：回答3个问题
  1. 分批错开(5批各1/5资金、起点错开1天)能否降低回撤？
  2. 哪个相位(周几买卖)表现最优？
  3. 5天持有的最优止损线是多少？(-6%/-8%/-10%/-12%/-15%/None)

方法：
  - 5个子批(phase 0~4)：从交易日序号i, i+1, i+2, i+3, i+4起，各滚动5天持有
  - 每个phase测试6种止损线：-6%, -8%, -10%, -12%, -15%, None
  - 输出：30个子策略指标 + 6个组合策略(5phase合并)指标
  
断点续跑：按 (phase, stop_loss) 组合checkpoint
运行：nohup python3 train_backtest_s023_staggered_stoploss_grid.py > grid_run.log 2>&1 &
"""
import json, sqlite3, time, os, sys
import numpy as np, pandas as pd
import lightgbm as lgb
from collections import defaultdict

BASE_DIR = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S013-喜神池LightGBM选股"
OUT_DIR  = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-26-S023-周度5天换仓"
PANEL_PATH = f"{BASE_DIR}/features_panel.pkl"
DB_PATH = "/Users/ziruzhu/stock-data/stock_all.db"
XISHEN_PATH = f"{BASE_DIR}/xishen_plus_pool.csv"

HORIZON = 5
HOLD_DAYS = 5  # 持有5个交易日
BACKTEST_START = "20170101"
TRAIN_MONTHS = 12
TOP_N = 20
PRICE_LIMIT = 500
BC, SC = 0.00025, 0.00125

# 止损网格：6个候选值
STOP_LOSS_GRID = [-0.06, -0.08, -0.10, -0.12, -0.15, None]
# 5个phase（相位），起点各错开1个交易日
N_PHASES = 5

CKPT = f"{OUT_DIR}/ckpt_grid.json"
LOG  = f"{OUT_DIR}/grid_run.log"
OUT_FILE = f"{OUT_DIR}/s023_staggered_stoploss_grid_result.json"

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

def run_one_strat(phase, stop_loss, panel, xishen_set, all_trade_dates, 
                  open_lookup, close_lookup, next_trade_date, real_px_idx, rebalance_dates):
    """运行单个子策略：phase=起点偏移(0~4), stop_loss=止损线"""
    sl_tag = f"{stop_loss*100:.0f}%" if stop_loss is not None else "None"
    log(f"  [Phase{phase} SL={sl_tag}] 开始...")
    
    panel_by_date = {d: sub for d, sub in panel.groupby("trade_date")}
    trades, nav, nav_curve = [], 1.0, []
    prev_holdings = set()
    total_stops, total_checks = 0, 0
    
    # phase偏移：起点从rebalance_dates[phase]开始，每HOLD_DAYS取一个
    my_rebal = rebalance_dates[phase::HOLD_DAYS]
    
    for i, rd in enumerate(my_rebal):
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
        
        # 股价≤500过滤
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
        
        # 买入日=打分日下一交易日，卖出日=买入日+HOLD_DAYS交易日后
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
        
        rets, valid_holdings, stop_count = [], [], 0
        for code in top20:
            try:
                entry_px = open_lookup.loc[(code, buy_date)]
                if pd.isna(entry_px) or entry_px <= 0:
                    continue
                
                # 逐日检查止损（buy_date次日起到sell_date前一日收盘）
                stop_hit, stop_px, d = False, None, buy_date
                while d < sell_date:
                    d_next = next_trade_date.get(d)
                    if d_next is None or d_next > sell_date:
                        break
                    try:
                        cl = close_lookup.loc[(code, d_next)]
                    except KeyError:
                        d = d_next; continue
                    if stop_loss is not None and not pd.isna(cl) and cl > 0 and cl/entry_px - 1 <= stop_loss:
                        stop_hit, stop_px = True, cl; break
                    d = d_next
                
                if stop_hit:
                    r = float(stop_px)/float(entry_px) - 1
                    stop_count += 1
                else:
                    try:
                        exit_px = close_lookup.loc[(code, sell_date)]
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
    
    if len(trades) == 0:
        return None
    
    # 计算指标
    pr = np.array([t["period_return"] for t in trades])
    n_years = (pd.to_datetime(trades[-1]["sell_date"]) - pd.to_datetime(trades[0]["buy_date"])).days/365.25
    nav_arr = np.array([1.0]+[c["nav"] for c in nav_curve])
    dd = (nav_arr/np.maximum.accumulate(nav_arr)-1).min()
    ann = nav**(1/n_years)-1 if n_years>0 and nav>0 else 0
    wr = float(np.mean(pr>0))
    ppy = 252/HOLD_DAYS
    sh = float(pr.mean()/pr.std()*np.sqrt(ppy)) if pr.std()>0 else 0
    
    log(f"    完成: 期数{len(trades)} 年化{ann*100:.2f}% 回撤{dd*100:.2f}% 夏普{sh:.2f} 胜率{wr*100:.1f}%")
    
    return {
        "phase": phase,
        "stop_loss": stop_loss,
        "n_periods": len(trades),
        "annual_return": round(ann, 4),
        "max_drawdown": round(float(dd), 4),
        "sharpe": round(sh, 4),
        "win_rate": round(wr, 4),
        "total_return": round(float(nav-1), 4),
        "total_stops": total_stops,
        "total_checks": total_checks,
        "nav_curve": nav_curve,
        "trades": trades
    }

def main():
    t0 = time.time()
    log("="*60)
    log(f"S023变体: 5天滚动 + 分批错开(5phase) + 止损网格({len(STOP_LOSS_GRID)}种)")
    
    pool = pd.read_csv(XISHEN_PATH)
    xishen_set = set(pool["ts_code"])
    log(f"喜神池: {len(xishen_set)} 只")
    
    log("读取特征面板...")
    panel = pd.read_pickle(PANEL_PATH)
    
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
    all_trade_dates = [d for d in all_trade_dates if d >= BACKTEST_START]
    rebalance_dates = all_trade_dates[::1]  # 每个交易日都可能是某phase的起点
    log(f"回测交易日: {len(all_trade_dates)}天")
    
    # 预加载真实价格（股价≤500过滤用）
    _con = sqlite3.connect(DB_PATH)
    _rp = ",".join(f"'{d}'" for d in rebalance_dates[::10])  # 抽样加载，降内存
    _rpx = pd.read_sql(f"SELECT ts_code,trade_date,close FROM daily WHERE trade_date IN ({_rp})", _con)
    _con.close()
    _rpx["close"] = pd.to_numeric(_rpx["close"], errors="coerce")
    _rpx["trade_date"] = _rpx["trade_date"].astype(str)
    real_px_idx = _rpx.set_index(["trade_date","ts_code"])["close"]
    
    open_lookup = panel.set_index(["ts_code", "trade_date"])["open_qfq"].sort_index()
    close_lookup = panel.set_index(["ts_code", "trade_date"])["close_qfq"].sort_index()
    next_trade_date = {d: all_trade_dates[i+1] for i, d in enumerate(all_trade_dates) if i+1 < len(all_trade_dates)}
    
    # 运行30个组合 (5 phase × 6 stop_loss)
    results = []
    for phase in range(N_PHASES):
        for sl in STOP_LOSS_GRID:
            res = run_one_strat(phase, sl, panel, xishen_set, all_trade_dates, 
                                open_lookup, close_lookup, next_trade_date, real_px_idx, rebalance_dates)
            if res:
                results.append(res)
    
    log(f"\n完成 {len(results)}/30 个子策略")
    
    # 生成6个组合策略（按stop_loss合并5个phase的nav）
    combined = []
    for sl in STOP_LOSS_GRID:
        sl_tag = f"{sl*100:.0f}%" if sl is not None else "None"
        subs = [r for r in results if r["stop_loss"] == sl]
        if len(subs) < N_PHASES:
            log(f"[组合 SL={sl_tag}] phase不全，跳过")
            continue
        
        # 合并nav：每个日期取5个phase的nav均值（等权）
        all_dates = sorted(set(sum([list(map(lambda x:x['date'], s['nav_curve'])) for s in subs], [])))
        combined_nav = []
        for d in all_dates:
            navs_at_d = []
            for s in subs:
                curve_dict = {c['date']: c['nav'] for c in s['nav_curve']}
                if d in curve_dict:
                    navs_at_d.append(curve_dict[d])
            if navs_at_d:
                combined_nav.append({"date": d, "nav": np.mean(navs_at_d)})
        
        if len(combined_nav) == 0:
            continue
        
        navs = np.array([c['nav'] for c in combined_nav])
        dates = [c['date'] for c in combined_nav]
        n_years = (pd.to_datetime(dates[-1]) - pd.to_datetime(dates[0])).days/365.25
        ann = navs[-1]**(1/n_years)-1 if n_years>0 and navs[-1]>0 else 0
        arr = np.array([1.0]+list(navs))
        dd = (arr/np.maximum.accumulate(arr)-1).min()
        
        # 合并所有trades算整体胜率
        all_trades = sum([s['trades'] for s in subs], [])
        pr = np.array([t['period_return'] for t in all_trades])
        wr = float(np.mean(pr>0)) if len(pr)>0 else 0
        sh = float(pr.mean()/pr.std()*np.sqrt(252/HOLD_DAYS)) if len(pr)>0 and pr.std()>0 else 0
        
        combined.append({
            "type": "combined_5phase",
            "stop_loss": sl,
            "annual_return": round(ann, 4),
            "max_drawdown": round(float(dd), 4),
            "sharpe": round(sh, 4),
            "win_rate": round(wr, 4),
            "total_return": round(float(navs[-1]-1), 4),
            "n_periods": len(all_trades),
            "nav_curve": combined_nav
        })
        log(f"[组合 SL={sl_tag}] 年化{ann*100:.2f}% 回撤{dd*100:.2f}% 夏普{sh:.2f} 胜率{wr*100:.1f}%")
    
    output = {
        "config": {"horizon": HORIZON, "hold_days": HOLD_DAYS, "top_n": TOP_N, 
                   "n_phases": N_PHASES, "stop_loss_grid": STOP_LOSS_GRID},
        "single_strategies": results,
        "combined_strategies": combined
    }
    json.dump(output, open(OUT_FILE, "w"), ensure_ascii=False, indent=2, default=str)
    log(f"\n结果已写出: {OUT_FILE}  总耗时{(time.time()-t0)/60:.1f}分钟")
    if os.path.exists(CKPT):
        os.remove(CKPT)

if __name__ == "__main__":
    main()
