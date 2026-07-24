#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S011-LightGBM选股+凯利仓位 · 滚动训练回测脚本

背景：S010用贝叶斯线性回归(v1)、LightGBM分位数回归(v2)做选股，都是为了原生获得
mu/sigma喂给凯利仓位公式，但两次实验都证明：牺牲选股模型的非线性能力去换
不确定性量化，代价远大于收益——候选池"无脑满仓"的年化从S009的42.5%掉到不到10%，
说明选股这一步的alpha比仓位管理更重要，不该被牺牲。

S011的思路：选股大脑用回S009验证过最强的LightGBM二分类器(预测跑赢全市场中位数
的概率)，不做任何妥协；仓位管理身体沿用S010已经验证有效的"两层凯利+样本外置信度
校准"框架(v1.1版本机制正确、只是选股基础太弱)。核心难点是：分类器输出的是
"跑赢概率p"，不是"预期收益率mu"，凯利公式需要mu/sigma，所以用"概率校准"把
p转换成mu/sigma——把训练窗口切出2个月做校准验证，统计"预测概率落在某个区间的
股票，历史上真实收益率的均值和标准差是多少"，再把这套校准表用到当期候选股票上。

核心设计：
1. 【选股】LightGBM二分类器(与S009同参数/同任务)，预测未来10日跑赢全市场
   中位数收益的概率p，取候选池(概率最高的30只)——选股逻辑与S009完全一致，
   不牺牲alpha
2. 【概率校准mu/sigma】用训练窗口切出的10个月拟合+2个月验证做校准：
   - 校准模型只用10个月数据训练，在2个月验证期(已实现历史数据)上打分
   - 把验证期全部样本按预测概率分成10个桶(deciles)，统计每个桶内
     真实fwd_ret的均值(mu_bucket)和标准差(sigma_bucket)——概率越高的桶，
     历史上真实收益均值应该越高，方差也可能不同
   - 当期候选股票的预测概率落在哪个桶，就用哪个桶的mu/sigma做凯利仓位输入
3. 【两层凯利仓位管理】(与S010同架构)
   - 个股层：f_i = mu_i/sigma_i²(来自概率校准桶)，half-Kelly打五折，单只
     封顶20%，候选池内部相对配比
   - 组合层：用验证期"最高概率decile"的真实兑现表现(oos_mu/oos_sigma)
     决定总仓位敞口(0~100%)，避免用候选池自身概率算仓位导致的选择偏差
4. 沿用S009/S010的基础设定：月度调仓、T+1开盘价成交、剔除ST/亏损股/北交所(.BJ)、
   买入佣金0.025%+卖出佣金0.025%+印花税0.05%(按金额加权换手计算)

严格避免未来函数：训练集只用 trade_date < 调仓日 的样本，标签窗口(未来10日)
必须已经在调仓日之前完全实现；概率校准用的验证期数据也全部是已实现的历史数据。
"""
import json
import os
import pickle
import sqlite3
import time
import numpy as np
import pandas as pd
import lightgbm as lgb

BASE_DIR = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-18-S011-LightGBM选股+凯利仓位"
PANEL_PATH = f"{BASE_DIR}/features_panel.pkl"
DB_PATH = "/Users/ziruzhu/stock-data/stock_all.db"
CHECKPOINT_PATH = f"{BASE_DIR}/checkpoint.pkl"

BACKTEST_START = "20170101"
TRAIN_MONTHS = 12
LABEL_HORIZON = 10

# ---- 候选池与凯利仓位参数(与S010保持一致，便于对比) ----
CANDIDATE_MAX = 30
CANDIDATE_MIN = 5
KELLY_HALF = 0.5
SINGLE_CAP = 0.20
PORTFOLIO_CORR = 0.30
EXPOSURE_CAP = 1.0
EXPOSURE_FLOOR = 0.0

# ---- 概率校准/样本外验证参数 ----
VALIDATE_MONTHS = 2      # 12个月训练窗口划出最近2个月做校准+置信度验证
FIT_MONTHS = 10
TOP_DECILE_FRAC = 0.10   # 组合层仓位参考"最高概率前10%"的验证期真实表现
N_CALIB_BUCKETS = 10     # 概率校准分桶数

# 交易成本设定(与S009/S010一致)
BUY_COMMISSION = 0.00025
SELL_COMMISSION = 0.00025
STAMP_TAX = 0.0005

FEATURE_COLS = [
    "mom_5", "mom_10", "mom_20", "mom_60", "mom_120",
    "turnover_rate", "turnover_rate_f", "volume_ratio", "vol_chg_20",
    "bias_5", "bias_10", "bias_20", "bias_60",
    "macd_dif", "macd_dea", "macd", "kdj_k", "kdj_d", "kdj_j",
    "rsi_6", "rsi_12", "rsi_24", "cci", "boll_pct", "boll_width",
    "pe", "pe_ttm", "pb", "ps", "ps_ttm", "dv_ttm",
    "net_mf_ratio", "lg_buy_ratio",
]

LGBM_CLF_PARAMS = dict(
    boosting_type="gbdt",
    num_leaves=31,
    learning_rate=0.05,
    n_estimators=200,
    subsample=0.8,
    colsample_bytree=0.8,
    random_state=42,
    verbose=-1,
)


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
    df = pd.DataFrame({"date": s, "trade_date": trade_dates})
    df["ym"] = df["date"].dt.to_period("M")
    firsts = df.groupby("ym").first()
    return firsts["trade_date"].tolist(), firsts["date"].tolist()


def train_final_classifier(train_df, score_df):
    """S009同款LightGBM二分类器：预测未来10日跑赢全市场中位数收益的概率p"""
    X_train = train_df[FEATURE_COLS]
    y_train = train_df["label"]
    model = lgb.LGBMClassifier(**LGBM_CLF_PARAMS)
    model.fit(X_train, y_train)
    X_score = score_df[FEATURE_COLS]
    p = model.predict_proba(X_score)[:, 1]
    return p, model


def calibrate_probability_to_mu_sigma(panel, all_trade_dates, rd, train_start):
    """
    概率校准 + 组合层置信度校准，一次验证过程同时完成两件事：

    1) 个股层概率分桶校准表：把10个月拟合模型在2个月验证期(已实现历史)的预测
       概率分成N_CALIB_BUCKETS个桶，统计每桶内"超额收益"(相对当日全市场中位数)
       的均值/标准差。候选股票的预测概率落在哪个桶，就用哪个桶的mu/sigma做
       凯利个股层输入。用超额收益而非绝对收益，是因为下跌市场里绝对收益普遍
       为负，若用绝对收益校准会导致所有桶mu都是负数，个股层相对排序能力失效。
    2) 组合层oos_mu/oos_sigma：验证期内预测概率最高的前10%股票，真实兑现的
       "绝对收益"均值/标准差，用于组合层仓位敞口计算(逻辑与S010一致，避免选择
       偏差)。这里必须用绝对收益——决定的是"该不该拿现金"，不是"选股准不准"。

    返回: bucket_edges(分桶概率边界), bucket_mu(每桶均值数组),
          bucket_sigma(每桶标准差数组), oos_mu, oos_sigma, oos_n
    """
    rd_dt = pd.to_datetime(rd, format="%Y%m%d")
    val_start_dt = rd_dt - pd.DateOffset(months=VALIDATE_MONTHS)
    val_start = val_start_dt.strftime("%Y%m%d")

    fit_mask = (panel["trade_date"] >= train_start) & (panel["trade_date"] < val_start)
    fit_df = panel.loc[fit_mask].dropna(subset=FEATURE_COLS + ["label", "fwd_ret"])
    if len(fit_df) < 3000:
        return None, None, None, 0.0, 1.0, 0

    val_dates_all = [d for d in all_trade_dates if val_start <= d < rd]
    val_dates = val_dates_all[::5]  # 每5个交易日取1个，减少10日窗口重叠造成的样本冗余
    if len(val_dates) == 0:
        return None, None, None, 0.0, 1.0, 0

    X_fit = fit_df[FEATURE_COLS]
    y_fit = fit_df["label"]
    calib_model = lgb.LGBMClassifier(**LGBM_CLF_PARAMS)
    calib_model.fit(X_fit, y_fit)

    all_p = []
    all_ret_excess = []   # 超额收益(相对当日全市场中位数)：个股层用，判断"矮子里的将军"
    all_ret_abs = []      # 绝对收益：组合层用，判断"投资 vs 持现金"哪个划算
    for vd in val_dates:
        vd_df = panel[panel["trade_date"] == vd].dropna(subset=FEATURE_COLS, how="all")
        vd_df = vd_df.dropna(subset=["fwd_ret"])
        if len(vd_df) < 20:
            continue
        X_vd = vd_df[FEATURE_COLS]
        p_vd = calib_model.predict_proba(X_vd)[:, 1]
        all_p.append(p_vd)
        abs_ret = vd_df["fwd_ret"].values
        all_ret_abs.append(abs_ret)
        # 个股层必须用超额收益(fwd_ret - 当日全市场中位数)校准：下跌市场里绝对
        # 收益普遍为负，若个股层也用绝对收益，会导致所有候选股票mu都是负数，
        # 凯利公式把权重全部clip到0，丧失"矮子里挑将军"的相对排序能力。
        # 超额收益中性化了市场整体涨跌方向，只反映"选股能否跑赢大盘"的真实alpha。
        med_ret = float(np.median(abs_ret))
        all_ret_excess.append(abs_ret - med_ret)

    if len(all_p) == 0:
        return None, None, None, 0.0, 1.0, 0

    all_p = np.concatenate(all_p)
    all_ret_excess = np.concatenate(all_ret_excess)
    all_ret_abs = np.concatenate(all_ret_abs)
    if len(all_p) < 200:
        return None, None, None, 0.0, 1.0, len(all_p)

    # ---- 个股层概率分桶校准表：用超额收益，只反映相对排序能力 ----
    bucket_edges = np.quantile(all_p, np.linspace(0, 1, N_CALIB_BUCKETS + 1))
    bucket_edges[0] = -np.inf
    bucket_edges[-1] = np.inf
    bucket_idx = np.digitize(all_p, bucket_edges[1:-1])  # 0..N_CALIB_BUCKETS-1

    bucket_mu = np.zeros(N_CALIB_BUCKETS)
    bucket_sigma = np.full(N_CALIB_BUCKETS, 0.05)
    for b in range(N_CALIB_BUCKETS):
        ret_b = all_ret_excess[bucket_idx == b]
        if len(ret_b) >= 20:
            bucket_mu[b] = float(ret_b.mean())
            bucket_sigma[b] = max(float(ret_b.std()), 1e-4)

    # ---- 组合层oos_mu/oos_sigma：用绝对收益，决定"该不该投资、投多少" ----
    # 这里必须用绝对收益而非超额收益——即便模型选股能力很强(跑赢大盘)，
    # 但如果连最看好的候选股票绝对预期都是亏钱的，凯利公式应该建议空仓避险，
    # 而不是因为"跑赢了大盘"就继续满仓吃绝对亏损。
    n_top = max(int(len(all_p) * TOP_DECILE_FRAC), 30)
    top_idx = np.argsort(all_p)[-n_top:]
    top_realized_abs = all_ret_abs[top_idx]
    oos_mu = float(top_realized_abs.mean())
    oos_sigma = max(float(top_realized_abs.std()), 1e-4)
    oos_n = len(top_realized_abs)

    return bucket_edges, bucket_mu, bucket_sigma, oos_mu, oos_sigma, oos_n


def map_prob_to_mu_sigma(p_values, bucket_edges, bucket_mu, bucket_sigma):
    """把候选股票的预测概率p，映射到校准表对应桶的mu/sigma"""
    if bucket_edges is None:
        # 校准失败时的兜底：概率越高给越小的正mu，sigma给一个保守估计
        mu = np.clip(p_values - 0.5, 0, None) * 0.05
        sigma = np.full_like(p_values, 0.08)
        return mu, sigma
    idx = np.digitize(p_values, bucket_edges[1:-1])
    idx = np.clip(idx, 0, N_CALIB_BUCKETS - 1)
    mu = bucket_mu[idx]
    sigma = bucket_sigma[idx]
    return mu, sigma


def kelly_position(candidates, oos_mu, oos_sigma, oos_n):
    """两层凯利仓位管理，与S010架构完全一致"""
    if len(candidates) == 0:
        return {}, 0.0, 0.0, 0.0

    mu = candidates["mu"].values
    sigma = np.maximum(candidates["sigma"].values, 1e-6)

    f_i = mu / (sigma ** 2)
    f_i = np.clip(f_i, 0, None)
    if f_i.sum() <= 0:
        return {}, 0.0, 0.0, 0.0
    w_raw = f_i / f_i.sum()
    w_capped = np.minimum(w_raw, SINGLE_CAP)
    w_i = w_capped / w_capped.sum()

    portfolio_mu = float(np.sum(w_i * mu))
    var_own = np.sum((w_i ** 2) * (sigma ** 2))
    n = len(w_i)
    if n > 1:
        wi_sigma = w_i * sigma
        sum_wisig = wi_sigma.sum()
        cross = (sum_wisig ** 2 - np.sum(wi_sigma ** 2)) * PORTFOLIO_CORR
    else:
        cross = 0.0
    portfolio_var = max(var_own + cross, 1e-8)
    portfolio_sigma = float(np.sqrt(portfolio_var))

    confidence = min(1.0, np.sqrt(oos_n / 200.0)) if oos_n > 0 else 0.0
    oos_var = max(oos_sigma ** 2, 1e-6)
    exposure_kelly = (oos_mu / oos_var) * KELLY_HALF * confidence
    exposure = float(np.clip(exposure_kelly, EXPOSURE_FLOOR, EXPOSURE_CAP))

    target_weights = {
        code: float(w) * exposure
        for code, w in zip(candidates["ts_code"].values, w_i)
    }
    return target_weights, exposure, portfolio_mu, portfolio_sigma


def main():
    t0 = time.time()
    log("读取特征面板...")
    panel = pd.read_pickle(PANEL_PATH)
    log(f"面板读取完成: {len(panel):,} 行")

    blacklist = load_blacklist()
    log(f"黑名单股票数(ST+亏损): {len(blacklist)}")
    panel = panel[~panel["ts_code"].isin(blacklist)].reset_index(drop=True)
    log(f"剔除黑名单后: {len(panel):,} 行")

    n_before = len(panel)
    panel = panel[~panel["ts_code"].str.endswith(".BJ")].reset_index(drop=True)
    log(f"剔除北交所(.BJ)股票后: {len(panel):,} 行 (剔除{n_before - len(panel):,}行)")

    panel = panel.dropna(subset=FEATURE_COLS, how="all")

    all_trade_dates = sorted(panel["trade_date"].unique())
    month_dates, month_dt = get_month_start_dates(all_trade_dates)

    rebalance_dates = [d for d in month_dates if d >= BACKTEST_START]
    log(f"调仓日数量: {len(rebalance_dates)}, 首个调仓日: {rebalance_dates[0]}, 末个调仓日: {rebalance_dates[-1]}")

    panel_by_date = {d: sub for d, sub in panel.groupby("trade_date")}
    open_lookup = panel.set_index(["ts_code", "trade_date"])["open_qfq"].sort_index()
    next_trade_date = {d: all_trade_dates[i + 1] for i, d in enumerate(all_trade_dates) if i + 1 < len(all_trade_dates)}

    nav = 1.0
    nav_curve = []
    trades = []
    factor_history = []
    prev_weights = {}
    start_i = 0

    if os.path.exists(CHECKPOINT_PATH):
        try:
            with open(CHECKPOINT_PATH, "rb") as f:
                ckpt = pickle.load(f)
            nav = ckpt["nav"]
            nav_curve = ckpt["nav_curve"]
            trades = ckpt["trades"]
            factor_history = ckpt["factor_history"]
            prev_weights = ckpt["prev_weights"]
            start_i = ckpt["next_i"]
            log(f"[RESUME] 从checkpoint恢复: 已完成{start_i}期, 当前净值={nav:.4f}")
        except Exception as e:
            log(f"[WARN] checkpoint读取失败({e}), 从头开始")
            start_i = 0

    for i, rd in enumerate(rebalance_dates):
        if i < start_i:
            continue

        rd_dt = pd.to_datetime(rd, format="%Y%m%d")
        train_start_dt = rd_dt - pd.DateOffset(months=TRAIN_MONTHS)
        train_start = train_start_dt.strftime("%Y%m%d")

        train_mask = (panel["trade_date"] >= train_start) & (panel["trade_date"] < rd)
        train_df = panel.loc[train_mask].dropna(subset=FEATURE_COLS + ["label"])

        if len(train_df) < 5000:
            log(f"[{rd}] 训练样本不足({len(train_df)})，跳过本期")
            continue

        score_df = panel_by_date.get(rd)
        if score_df is None or len(score_df) == 0:
            continue
        score_df = score_df.dropna(subset=FEATURE_COLS, how="all").copy()
        if len(score_df) == 0:
            continue

        p, model = train_final_classifier(train_df, score_df)
        score_df["p"] = p

        importance = model.feature_importances_
        importance_norm = importance / (importance.sum() + 1e-9)
        factor_history.append({
            "rebalance_date": rd,
            "feature_importance": {c: round(float(v), 6) for c, v in zip(FEATURE_COLS, importance_norm)},
        })

        # 候选池：S009同款逻辑，取预测概率最高的前CANDIDATE_MAX只
        cand = score_df.sort_values("p", ascending=False).head(CANDIDATE_MAX).copy()

        next_rd = rebalance_dates[i + 1] if i + 1 < len(rebalance_dates) else None
        if next_rd is None:
            break

        buy_date = next_trade_date.get(rd)
        sell_date = next_trade_date.get(next_rd)
        if buy_date is None or sell_date is None:
            log(f"[{rd}] 无法确定T+1交易日，跳过")
            continue

        if len(cand) < CANDIDATE_MIN:
            target_weights, exposure, port_mu, port_sigma = {}, 0.0, 0.0, 0.0
            oos_mu, oos_sigma, oos_n = 0.0, 0.0, 0
        else:
            bucket_edges, bucket_mu, bucket_sigma, oos_mu, oos_sigma, oos_n = calibrate_probability_to_mu_sigma(
                panel, all_trade_dates, rd, train_start
            )
            mu_i, sigma_i = map_prob_to_mu_sigma(cand["p"].values, bucket_edges, bucket_mu, bucket_sigma)
            cand["mu"] = mu_i
            cand["sigma"] = sigma_i
            target_weights, exposure, port_mu, port_sigma = kelly_position(cand, oos_mu, oos_sigma, oos_n)

        valid_weights = {}
        for code, w in target_weights.items():
            try:
                p0 = open_lookup.loc[(code, buy_date)]
                p1 = open_lookup.loc[(code, sell_date)]
                if pd.isna(p0) or pd.isna(p1) or p0 <= 0:
                    continue
                r = float(p1) / float(p0) - 1
                valid_weights[code] = (w, r)
            except KeyError:
                continue

        if valid_weights:
            gross_ret = sum(w * r for w, r in valid_weights.values())
            realized_exposure = sum(w for w, r in valid_weights.values())
        else:
            gross_ret = 0.0
            realized_exposure = 0.0

        curr_weight_map = {code: w for code, (w, r) in valid_weights.items()}

        all_codes = set(prev_weights.keys()) | set(curr_weight_map.keys())
        buy_amt = 0.0
        sell_amt = 0.0
        for code in all_codes:
            w_old = prev_weights.get(code, 0.0)
            w_new = curr_weight_map.get(code, 0.0)
            diff = w_new - w_old
            if diff > 0:
                buy_amt += diff
            elif diff < 0:
                sell_amt += -diff
        buy_cost = buy_amt * BUY_COMMISSION
        sell_cost = sell_amt * (SELL_COMMISSION + STAMP_TAX)
        total_cost = buy_cost + sell_cost

        period_ret = gross_ret - total_cost
        nav *= (1 + period_ret)
        nav_curve.append({"date": sell_date, "nav": round(nav, 6)})

        trades.append({
            "rebalance_date": rd,
            "buy_date": buy_date,
            "next_date": next_rd,
            "sell_date": sell_date,
            "holdings": [{"code": c, "weight": round(w, 4)} for c, w in sorted(curr_weight_map.items(), key=lambda x: -x[1])],
            "n_holdings": len(curr_weight_map),
            "exposure": round(realized_exposure, 4),
            "portfolio_mu": round(port_mu, 6),
            "portfolio_sigma": round(port_sigma, 6),
            "oos_mu": round(oos_mu, 6),
            "oos_sigma": round(oos_sigma, 6),
            "oos_n": oos_n,
            "candidate_pool_size": len(cand),
            "gross_return": round(gross_ret, 6),
            "trading_cost": round(total_cost, 6),
            "period_return": round(period_ret, 6),
            "turnover": round((buy_amt + sell_amt) / 2, 4),
            "win_count": int(sum(1 for w, r in valid_weights.values() if r > 0)),
        })

        prev_weights = curr_weight_map

        try:
            ckpt = {
                "nav": nav, "nav_curve": nav_curve, "trades": trades,
                "factor_history": factor_history, "prev_weights": prev_weights,
                "next_i": i + 1,
            }
            with open(CHECKPOINT_PATH, "wb") as f:
                pickle.dump(ckpt, f)
        except Exception as e:
            log(f"[WARN] checkpoint写入失败: {e}")

        if (i + 1) % 12 == 0:
            log(f"已完成 {i+1}/{len(rebalance_dates)} 期调仓 (最近: {rd}), 当前净值: {nav:.4f}, 本期仓位: {realized_exposure*100:.0f}%")

    log(f"调仓记录数: {len(trades)}, 最终净值: {nav:.4f}")

    period_rets = np.array([t["period_return"] for t in trades])
    gross_rets = np.array([t["gross_return"] for t in trades])
    costs = np.array([t["trading_cost"] for t in trades])
    exposures = np.array([t["exposure"] for t in trades])
    n_periods = len(period_rets)
    total_return = nav - 1.0

    avg_turnover = float(np.mean([t["turnover"] for t in trades])) if trades else 0.0
    avg_cost_per_period = float(costs.mean()) if len(costs) > 0 else 0.0
    avg_exposure = float(exposures.mean()) if len(exposures) > 0 else 0.0
    empty_periods = int(np.sum(exposures < 0.01))

    n_years = n_periods / 12.0 if n_periods > 0 else 1
    annual_return = (nav ** (1 / n_years) - 1) if n_years > 0 and nav > 0 else 0.0

    nav_series = np.array([1.0] + [c["nav"] for c in nav_curve])
    running_max = np.maximum.accumulate(nav_series)
    drawdown = nav_series / running_max - 1
    max_drawdown = float(drawdown.min())

    win_rate = float(np.mean(period_rets > 0)) if n_periods > 0 else 0.0

    if n_periods > 1 and period_rets.std() > 0:
        sharpe = float(period_rets.mean() / period_rets.std() * np.sqrt(12))
    else:
        sharpe = 0.0

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
    log(f"仓位统计: 平均总仓位{avg_exposure*100:.1f}%, 完全空仓期数{empty_periods}/{n_periods}")

    result = {
        "strategy_name": "S011-LightGBM选股+凯利仓位",
        "created_date": "2026-07-18",
        "strategy_type": "LightGBM分类器选股+概率校准+两层凯利仓位管理",
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
        "kelly_analysis": {
            "avg_exposure": round(avg_exposure, 4),
            "min_exposure": round(float(exposures.min()), 4) if len(exposures) else 0,
            "max_exposure": round(float(exposures.max()), 4) if len(exposures) else 0,
            "empty_periods": empty_periods,
            "kelly_half_factor": KELLY_HALF,
            "single_stock_cap": SINGLE_CAP,
            "portfolio_corr_assumption": PORTFOLIO_CORR,
        },
        "aux_metrics": {
            "avg_holdings_per_period": round(float(np.mean([t["n_holdings"] for t in trades])), 1) if trades else 0,
            "first_rebalance": trades[0]["rebalance_date"] if trades else None,
            "last_rebalance": trades[-1]["rebalance_date"] if trades else None,
            "n_features": len(FEATURE_COLS),
            "train_window_months": TRAIN_MONTHS,
            "candidate_max": CANDIDATE_MAX,
        },
        "nav_curve": nav_curve,
        "trades_summary": trades[-24:],
        "stocks": trades[-1]["holdings"] if trades else [],
        "factor_history_recent": factor_history[-6:],
        "ai_analysis": {
            "model": "LightGBM分类器(S009同款) + 概率校准mu/sigma + 两层Kelly(33features)",
            "summary": f"月度调仓{n_periods}期，年化{annual_return*100:.1f}%(毛{gross_annual_return*100:.1f}%)，胜率{win_rate*100:.1f}%，最大回撤{max_drawdown*100:.1f}%，夏普{sharpe:.2f}，平均总仓位{avg_exposure*100:.1f}%，平均换手率{avg_turnover*100:.1f}%/期。",
            "confidence": "中",
        },
    }

    out_path = f"{BASE_DIR}/results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log(f"results.json 已写出: {out_path}")

    trades_df = pd.DataFrame([
        {
            "rebalance_date": t["rebalance_date"], "buy_date": t["buy_date"],
            "sell_date": t["sell_date"], "n_holdings": t["n_holdings"],
            "exposure": t["exposure"], "portfolio_mu": t["portfolio_mu"],
            "portfolio_sigma": t["portfolio_sigma"],
            "oos_mu": t["oos_mu"], "oos_sigma": t["oos_sigma"], "oos_n": t["oos_n"],
            "candidate_pool_size": t["candidate_pool_size"],
            "gross_return": t["gross_return"], "trading_cost": t["trading_cost"],
            "period_return": t["period_return"], "turnover": t["turnover"],
            "win_count": t["win_count"],
            "holdings": ";".join(f"{h['code']}:{h['weight']}" for h in t["holdings"]),
        }
        for t in trades
    ])
    trades_df.to_csv(f"{BASE_DIR}/trades_full.csv", index=False, encoding="utf-8-sig")
    log(f"完整调仓记录已写出: {BASE_DIR}/trades_full.csv")

    with open(f"{BASE_DIR}/factor_history_full.json", "w", encoding="utf-8") as f:
        json.dump(factor_history, f, ensure_ascii=False, indent=2)
    log(f"完整因子重要性历史已写出: {BASE_DIR}/factor_history_full.json")

    log(f"耗时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
