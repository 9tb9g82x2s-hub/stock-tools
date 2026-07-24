#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S009-LightGBM多因子选股 · 滚动训练回测脚本

逻辑：
1. 读取 features_panel.parquet（build_panel.py 产出）
2. 剔除 ST / 亏损股（blacklist_st / blacklist_loss）
3. 按月度调仓：每月第一个交易日，用过去12个月数据滚动训练 LightGBM 分类模型，
   对当日全市场股票打分，选 Top20 等权买入，持有到下月初再调仓
4. 严格避免未来函数：训练集的标签窗口（未来10日）必须在调仓日之前已经实现，
   即训练集只使用 "调仓日 - 10个交易日" 之前的样本（标签已知）
5. 【执行价用T+1开盘价】调仓日收盘后才能算出分数，现实中当天已无法成交，
   只能在下一个交易日开盘时才能真实下单买入/卖出。回测按调仓日次一交易日的
   前复权开盘价(open_qfq)成交，不用调仓日当天的收盘价，更贴近实盘可执行性。
6. 【计入交易成本】按实际换手（本期持仓与上期持仓的重叠度）计算：
   - 买入佣金 0.025%（万2.5，主流互联网券商标准）
   - 卖出佣金 0.025% + 印花税 0.05%（2023年8月起现行税率，仅卖出收取）
   - 只对"新买入"和"被卖出"的部分收费，持仓不变的股票不产生费用
7. 输出净值曲线、逐笔换仓记录、核心指标到 results.json
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

BACKTEST_START = "20170101"  # 回测开始（留出2016年作为首次训练窗口）
TRAIN_MONTHS = 12
TOP_N = 20
LABEL_HORIZON = 10

# 交易成本设定（A股现行标准，2023年8月印花税下调后）
BUY_COMMISSION = 0.00025   # 买入佣金 万2.5
SELL_COMMISSION = 0.00025  # 卖出佣金 万2.5
STAMP_TAX = 0.0005         # 印花税 万5，仅卖出收取

FEATURE_COLS = [
    "mom_5", "mom_10", "mom_20", "mom_60", "mom_120",
    "turnover_rate", "turnover_rate_f", "volume_ratio", "vol_chg_20",
    "bias_5", "bias_10", "bias_20", "bias_60",
    "macd_dif", "macd_dea", "macd", "kdj_k", "kdj_d", "kdj_j",
    "rsi_6", "rsi_12", "rsi_24", "cci", "boll_pct", "boll_width",
    "pe", "pe_ttm", "pb", "ps", "ps_ttm", "dv_ttm",
    "net_mf_ratio", "lg_buy_ratio",
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
    """从全部交易日列表中提取每月第一个交易日"""
    s = pd.Series(pd.to_datetime(trade_dates, format="%Y%m%d"))
    df = pd.DataFrame({"date": s, "trade_date": trade_dates})
    df["ym"] = df["date"].dt.to_period("M")
    firsts = df.groupby("ym").first()
    return firsts["trade_date"].tolist(), firsts["date"].tolist()


def train_and_score(train_df, score_df):
    X_train = train_df[FEATURE_COLS]
    y_train = train_df["label"]

    model = lgb.LGBMClassifier(
        boosting_type="gbdt",
        num_leaves=31,
        learning_rate=0.05,
        n_estimators=200,
        subsample=0.8,
        colsample_bytree=0.8,
        random_state=42,
        verbose=-1,
    )
    model.fit(X_train, y_train)

    X_score = score_df[FEATURE_COLS]
    scores = model.predict_proba(X_score)[:, 1]
    return scores, model


def main():
    t0 = time.time()
    log("读取特征面板...")
    panel = pd.read_pickle(PANEL_PATH)
    log(f"面板读取完成: {len(panel):,} 行")

    blacklist = load_blacklist()
    log(f"黑名单股票数(ST+亏损): {len(blacklist)}")
    panel = panel[~panel["ts_code"].isin(blacklist)].reset_index(drop=True)
    log(f"剔除黑名单后: {len(panel):,} 行")

    # 老大没有北交所交易权限，剔除所有.BJ股票，避免选出无法实盘买入的标的
    n_before = len(panel)
    panel = panel[~panel["ts_code"].str.endswith(".BJ")].reset_index(drop=True)
    log(f"剔除北交所(.BJ)股票后: {len(panel):,} 行 (剔除{n_before - len(panel):,}行)")

    # 丢弃特征或标签全空的行
    panel = panel.dropna(subset=FEATURE_COLS, how="all")

    all_trade_dates = sorted(panel["trade_date"].unique())
    month_dates, month_dt = get_month_start_dates(all_trade_dates)

    # 回测区间：调仓日 >= BACKTEST_START
    rebalance_dates = [d for d in month_dates if d >= BACKTEST_START]
    log(f"调仓日数量: {len(rebalance_dates)}, 首个调仓日: {rebalance_dates[0]}, 末个调仓日: {rebalance_dates[-1]}")

    panel_by_date = {d: sub for d, sub in panel.groupby("trade_date")}
    # 快速价格查表：(ts_code, trade_date) -> open_qfq（排序后 MultiIndex 查询更快）
    # 用开盘价而非收盘价：调仓日收盘后才知道打分结果，实际下单只能在下一交易日执行
    open_lookup = panel.set_index(["ts_code", "trade_date"])["open_qfq"].sort_index()

    # T+1交易日映射：调仓日 -> 下一个真实交易日（用于确定实际下单/成交日）
    next_trade_date = {d: all_trade_dates[i + 1] for i, d in enumerate(all_trade_dates) if i + 1 < len(all_trade_dates)}

    nav = 1.0
    nav_curve = []  # (date, nav)
    trades = []  # 每期持仓记录及收益
    prev_holdings = set()  # 上一期持仓，用于计算换手率

    for i, rd in enumerate(rebalance_dates):
        rd_dt = pd.to_datetime(rd, format="%Y%m%d")
        train_start_dt = rd_dt - pd.DateOffset(months=TRAIN_MONTHS)
        train_start = train_start_dt.strftime("%Y%m%d")

        # 标签实现的安全边界：只用 trade_date < rd 的样本，且该样本的label非空
        train_mask = (panel["trade_date"] >= train_start) & (panel["trade_date"] < rd)
        train_df = panel.loc[train_mask].dropna(subset=FEATURE_COLS + ["label"])

        if len(train_df) < 5000:
            log(f"[{rd}] 训练样本不足({len(train_df)})，跳过本期")
            continue

        score_df = panel_by_date.get(rd)
        if score_df is None or len(score_df) == 0:
            continue
        score_df = score_df.dropna(subset=FEATURE_COLS, how="all").copy()

        scores, model = train_and_score(train_df, score_df)
        score_df = score_df.copy()
        score_df["score"] = scores
        top20 = score_df.sort_values("score", ascending=False).head(TOP_N)["ts_code"].tolist()

        next_rd = rebalance_dates[i + 1] if i + 1 < len(rebalance_dates) else None
        if next_rd is None:
            break

        # T+1开盘价成交：rd当天算出分数，buy_date(rd次日)开盘买入；
        # next_rd当天算出新分数，sell_date(next_rd次日)开盘卖出/换仓
        buy_date = next_trade_date.get(rd)
        sell_date = next_trade_date.get(next_rd)
        if buy_date is None or sell_date is None:
            log(f"[{rd}] 无法确定T+1交易日，跳过")
            continue

        rets = []
        valid_holdings = []
        for code in top20:
            try:
                p0 = open_lookup.loc[(code, buy_date)]
                p1 = open_lookup.loc[(code, sell_date)]
                if pd.isna(p0) or pd.isna(p1) or p0 <= 0:
                    continue
                r = float(p1) / float(p0) - 1
                rets.append(r)
                valid_holdings.append(code)
            except KeyError:
                continue

        if len(rets) == 0:
            log(f"[{rd}] 无有效持仓收益，跳过")
            continue

        gross_ret = float(np.mean(rets))  # 等权持仓，未计成本的毛收益

        # ---- 计算交易成本 ----
        # 本期新买入的股票 = 本期持仓 - 上期持仓（这部分要付买入佣金）
        # 本期卖出的股票 = 上期持仓 - 本期持仓（这部分要付卖出佣金+印花税）
        curr_holdings = set(valid_holdings)
        bought = curr_holdings - prev_holdings
        sold = prev_holdings - curr_holdings
        n_curr = len(curr_holdings) if curr_holdings else 1
        n_prev = len(prev_holdings) if prev_holdings else 1

        buy_turnover = len(bought) / n_curr if n_curr > 0 else 0.0
        sell_turnover = len(sold) / n_prev if n_prev > 0 else 0.0

        buy_cost = buy_turnover * BUY_COMMISSION
        sell_cost = sell_turnover * (SELL_COMMISSION + STAMP_TAX)
        total_cost = buy_cost + sell_cost

        period_ret = gross_ret - total_cost  # 扣除交易成本后的净收益
        nav *= (1 + period_ret)
        nav_curve.append({"date": sell_date, "nav": round(nav, 6)})

        trades.append({
            "rebalance_date": rd,
            "buy_date": buy_date,
            "next_date": next_rd,
            "sell_date": sell_date,
            "holdings": valid_holdings,
            "n_holdings": len(valid_holdings),
            "gross_return": round(gross_ret, 6),
            "trading_cost": round(total_cost, 6),
            "period_return": round(period_ret, 6),
            "buy_turnover": round(buy_turnover, 4),
            "sell_turnover": round(sell_turnover, 4),
            "win_count": int(sum(1 for r in rets if r > 0)),
        })

        prev_holdings = curr_holdings

        if (i + 1) % 12 == 0:
            log(f"已完成 {i+1}/{len(rebalance_dates)} 期调仓 (最近: {rd}), 当前净值: {nav:.4f}")

    log(f"调仓记录数: {len(trades)}, 最终净值: {nav:.4f}")

    # ---- 计算核心指标 ----
    period_rets = np.array([t["period_return"] for t in trades])
    gross_rets = np.array([t["gross_return"] for t in trades])
    costs = np.array([t["trading_cost"] for t in trades])
    n_periods = len(period_rets)
    total_return = nav - 1.0

    avg_turnover = float(np.mean([(t["buy_turnover"] + t["sell_turnover"]) / 2 for t in trades])) if trades else 0.0
    avg_cost_per_period = float(costs.mean()) if len(costs) > 0 else 0.0
    total_cost_drag = float(costs.sum())  # 累计成本拖累（简单加总，非复利口径，仅作参考）

    n_years = n_periods / 12.0 if n_periods > 0 else 1
    annual_return = (nav ** (1 / n_years) - 1) if n_years > 0 and nav > 0 else 0.0

    # 最大回撤（基于净值曲线）
    nav_series = np.array([1.0] + [c["nav"] for c in nav_curve])
    running_max = np.maximum.accumulate(nav_series)
    drawdown = nav_series / running_max - 1
    max_drawdown = float(drawdown.min())

    win_rate = float(np.mean(period_rets > 0)) if n_periods > 0 else 0.0

    # 夏普比率（以月度收益年化，无风险利率简化为0）
    if n_periods > 1 and period_rets.std() > 0:
        sharpe = float(period_rets.mean() / period_rets.std() * np.sqrt(12))
    else:
        sharpe = 0.0

    # 毛收益（未计成本）年化，用于对比成本拖累幅度
    gross_nav = float(np.prod(1 + gross_rets)) if len(gross_rets) > 0 else 1.0
    gross_annual_return = (gross_nav ** (1 / n_years) - 1) if n_years > 0 and gross_nav > 0 else 0.0

    metrics = {
        "total_return": round(float(total_return), 4),
        "annual_return": round(float(annual_return), 4),
        "win_rate": round(win_rate, 4),
        "max_drawdown": round(max_drawdown, 4),
        "sharpe_ratio": round(sharpe, 4),
        "total_trades": n_periods,
    }
    log(f"指标汇总: {metrics}")
    log(f"成本影响: 毛年化{gross_annual_return*100:.2f}% -> 净年化{annual_return*100:.2f}%, 平均单期换手率{avg_turnover*100:.1f}%, 平均单期成本{avg_cost_per_period*100:.3f}%")

    # ---- 输出 results.json ----
    result = {
        "strategy_name": "S009-LightGBM多因子选股",
        "created_date": "2026-07-16",
        "strategy_type": "多因子机器学习",
        "metrics": metrics,
        "cost_analysis": {
            "gross_annual_return": round(float(gross_annual_return), 4),
            "net_annual_return": round(float(annual_return), 4),
            "cost_drag_annualized": round(float(gross_annual_return - annual_return), 4),
            "avg_turnover_per_period": round(avg_turnover, 4),
            "avg_cost_per_period": round(avg_cost_per_period, 6),
            "buy_commission_rate": BUY_COMMISSION,
            "sell_commission_rate": SELL_COMMISSION,
            "stamp_tax_rate": STAMP_TAX,
        },
        "aux_metrics": {
            "avg_holdings_per_period": round(float(np.mean([t["n_holdings"] for t in trades])), 1) if trades else 0,
            "first_rebalance": trades[0]["rebalance_date"] if trades else None,
            "last_rebalance": trades[-1]["rebalance_date"] if trades else None,
            "n_features": len(FEATURE_COLS),
            "train_window_months": TRAIN_MONTHS,
            "top_n": TOP_N,
        },
        "nav_curve": nav_curve,
        "trades_summary": trades[-24:],  # 只保留最近24期明细，避免文件过大
        "stocks": [
            {"code": c, "signal_date": trades[-1]["rebalance_date"]} for c in trades[-1]["holdings"]
        ] if trades else [],
        "ai_analysis": {
            "model": "LightGBM (gbdt, 32features)",
            "summary": f"月度调仓{n_periods}期，年化{annual_return*100:.1f}%(毛{gross_annual_return*100:.1f}%)，胜率{win_rate*100:.1f}%，最大回撤{max_drawdown*100:.1f}%，夏普{sharpe:.2f}，平均换手率{avg_turnover*100:.1f}%/期。",
            "confidence": "中",
        },
    }

    out_path = f"{BASE_DIR}/results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log(f"results.json 已写出: {out_path}")

    # 保存完整调仓记录（供后续分析）
    trades_df = pd.DataFrame(trades)
    trades_df.to_csv(f"{BASE_DIR}/trades_full.csv", index=False, encoding="utf-8-sig")
    log(f"完整调仓记录已写出: {BASE_DIR}/trades_full.csv")

    log(f"耗时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
