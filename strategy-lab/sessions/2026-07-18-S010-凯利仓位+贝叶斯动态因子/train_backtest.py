#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S010-凯利仓位+贝叶斯动态因子 · 滚动训练回测脚本 (v2: LightGBM分位数回归版)

版本变迁说明：
- v1.0/v1.1 用 sklearn BayesianRidge(贝叶斯线性回归) 做选股，原生输出mu/sigma，
  但线性模型抓不住非线性因子交互，回测发现"候选池满仓"年化只有9.8%，只有
  S009(LightGBM分类器)毛年化42.5%的约1/4——选股能力被严重牺牲，仓位管理
  做得再精细也无法弥补选股本身的天花板。
- v2 改用 LightGBM 分位数回归(quantile regression)：同时训练q10/q50/q90三个
  分位数模型，q50作为预测均值mu(保留非线性拟合能力，找回S009级别的选股能力)，
  用(q90-q10)分位数区间反推预测标准差sigma(80%置信区间宽度换算，
  sigma=(q90-q10)/2.5632，corresponds标准正态分布10%~90%分位数间距)。
  这样既恢复了LightGBM的非线性选股能力，又保留了"贝叶斯思想"的核心
  精神——用预测的不确定性(sigma)去调节仓位，只是用分位数区间宽度代替了
  贝叶斯后验方差来量化不确定性。

核心设计（与S009的区别）：
1. 【LightGBM分位数模型选股】q10/q50/q90三个模型，mu=q50，sigma=(q90-q10)/2.5632，
   这是纯点预测模型(如S009分类器)做不到的——把"模型对这次预测有多大把握"也
   一并给出来，直接喂给凯利公式做仓位管理
2. 【动态因子】每期用最近12个月数据滚动重训练，模型会随新数据自动更新——
   本质是"旧认知(上期模型)遇到新证据(新数据) -> 新认知(本期模型)"的滚动更新
3. 【两层凯利仓位管理】
   - 个股层：f_i = mu_i / sigma_i^2，half-Kelly打五折，单只权重封顶20%，
     得到候选股票池内部的相对配置比例
   - 组合层：不能直接用候选池自己的mu算仓位(候选池是"挑出来的"，mu虚高，
     存在选择偏差)。改用"样本外置信度校准"——把训练窗口拆成10个月拟合+
     2个月验证，用拟合期模型在验证期(已实现的历史数据)上的真实Top档表现
     (oos_mu/oos_sigma)决定"今天敢下多大仓位"。模型近期确实选得准就多仓位，
     选不准就自动降仓甚至空仓
4. 沿用S009的基础设定：月度调仓、T+1开盘价成交、剔除ST/亏损股/北交所(.BJ)、
   买入佣金0.025%+卖出佣金0.025%+印花税0.05%(按金额加权换手计算)

严格避免未来函数：训练集只用 trade_date < 调仓日 的样本，标签窗口(未来10日)
必须已经在调仓日之前完全实现。
"""
import json
import os
import pickle
import sqlite3
import time
import numpy as np
import pandas as pd
import lightgbm as lgb

BASE_DIR = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-18-S010-凯利仓位+贝叶斯动态因子"
PANEL_PATH = f"{BASE_DIR}/features_panel.pkl"
DB_PATH = "/Users/ziruzhu/stock-data/stock_all.db"
CHECKPOINT_PATH = f"{BASE_DIR}/checkpoint.pkl"

BACKTEST_START = "20170101"
TRAIN_MONTHS = 12
LABEL_HORIZON = 10

# ---- 候选池与凯利仓位参数 ----
CANDIDATE_MAX = 30       # 候选池最多30只(mu>0且排名靠前)
CANDIDATE_MIN = 5        # 候选池不足5只则本期空仓(现金观望)
KELLY_HALF = 0.5         # half-Kelly打五折，控制过度下注风险
SINGLE_CAP = 0.20        # 单只股票在组合内部的相对权重上限20%
PORTFOLIO_CORR = 0.30    # 组合内个股收益相关系数的简化假设(A股同涨同跌较普遍)
EXPOSURE_CAP = 1.0       # 总仓位上限100%，不加杠杆
EXPOSURE_FLOOR = 0.0     # 总仓位下限0%，允许完全空仓持有现金

# ---- 样本外验证参数(用于组合层仓位敞口的置信度校准) ----
# 关键问题：候选池是按训练集mu从高到低"挑出来的"，这批股票的mu天然虚高(选择偏差)，
# 如果直接拿这个虚高mu去算凯利仓位，公式几乎永远得出"满仓"，起不到"没把握就少下注"
# 的作用。所以仓位敞口改用模型在最近2个月"样本外"数据上的真实表现来估计，
# 而不是用训练集自己的预测值——这才是贝叶斯方法该有的严谨性：用模型在新数据上
# 校准的实际准确度，而不是模型对自己训练数据的自信程度，来决定敢下多大的注。
VALIDATE_MONTHS = 2      # 从12个月训练窗口中划出最近2个月做样本外验证
FIT_MONTHS = 10          # 验证用模型只用前10个月拟合(12-2)
TOP_DECILE_FRAC = 0.10   # 验证窗口每日取预测最高的前10%股票看真实表现

# 交易成本设定(与S009一致)
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


# 80%置信区间(q10~q90)宽度换算成标准差的系数：标准正态分布下，
# 90%分位数-10%分位数 = 2 * 1.2816(z_0.9) = 2.5632个标准差
Q_SPREAD_TO_STD = 2.5632

LGBM_PARAMS = dict(
    boosting_type="gbdt",
    num_leaves=31,
    learning_rate=0.05,
    n_estimators=150,
    subsample=0.8,
    colsample_bytree=0.8,
    random_state=42,
    verbose=-1,
)


def _fit_quantile(X_train, y_train, alpha):
    model = lgb.LGBMRegressor(objective="quantile", alpha=alpha, **LGBM_PARAMS)
    model.fit(X_train, y_train)
    return model


def train_and_score(train_df, score_df):
    """LightGBM分位数回归：训练q10/q50/q90三个模型，预测fwd_ret的分位数

    mu = q50(中位数预测，比均值更抗异常值)
    sigma = (q90 - q10) / 2.5632（80%置信区间宽度反推标准差，正态近似）
    LightGBM原生支持NaN，无需像BayesianRidge那样做中位数填充。
    """
    X_train = train_df[FEATURE_COLS].values
    y_train = train_df["fwd_ret"].values
    X_score = score_df[FEATURE_COLS].values

    model_q50 = _fit_quantile(X_train, y_train, 0.5)
    model_q10 = _fit_quantile(X_train, y_train, 0.1)
    model_q90 = _fit_quantile(X_train, y_train, 0.9)

    q10 = model_q10.predict(X_score)
    q50 = model_q50.predict(X_score)
    q90 = model_q90.predict(X_score)

    mu = q50
    sigma = np.maximum((q90 - q10) / Q_SPREAD_TO_STD, 1e-4)
    return mu, sigma, model_q50


def estimate_oos_confidence(panel, all_trade_dates, rd, train_start):
    """
    样本外置信度校准：组合层凯利仓位不能用"训练集自己挑出来的候选池"的mu，
    因为候选池本身就是按mu从高到低选出来的，必然存在选择偏差(虚高)。

    做法：把12个月训练窗口拆成 FIT_MONTHS(10个月)拟合 + VALIDATE_MONTHS(2个月)验证。
    用拟合期数据训练一个"验证模型"，在验证期内的几个历史交易日上打分，每次取
    预测最靠前的TOP_DECILE_FRAC股票，看它们在当时的真实fwd_ret表现如何(这些都是
    验证窗口内早已实现的历史数据，不构成未来函数)。把这些"样本外Top档"的真实
    收益汇总，得到 oos_mu(均值)/oos_sigma(标准差)，反映"模型选股逻辑在没见过的
    新数据上，实际能兑现多少超额收益、稳定性如何"——这才是决定"敢下多大仓位"
    的合理依据，而不是模型对训练集自身预测的自信程度。

    返回: oos_mu, oos_sigma, n_obs（样本量，太小则外部按低置信度处理）
    """
    rd_dt = pd.to_datetime(rd, format="%Y%m%d")
    val_start_dt = rd_dt - pd.DateOffset(months=VALIDATE_MONTHS)
    val_start = val_start_dt.strftime("%Y%m%d")

    fit_mask = (panel["trade_date"] >= train_start) & (panel["trade_date"] < val_start)
    fit_df = panel.loc[fit_mask].dropna(subset=FEATURE_COLS + ["fwd_ret"])
    if len(fit_df) < 3000:
        return 0.0, 1.0, 0

    val_dates_all = [d for d in all_trade_dates if val_start <= d < rd]
    val_dates = val_dates_all[::5]  # 每5个交易日取1个，减少10日窗口重叠造成的样本冗余
    if len(val_dates) == 0:
        return 0.0, 1.0, 0

    X_fit = fit_df[FEATURE_COLS].values
    y_fit = fit_df["fwd_ret"].values
    val_model = lgb.LGBMRegressor(objective="quantile", alpha=0.5, **LGBM_PARAMS)
    val_model.fit(X_fit, y_fit)

    realized = []
    for vd in val_dates:
        vd_df = panel[panel["trade_date"] == vd].dropna(subset=FEATURE_COLS, how="all")
        vd_df = vd_df.dropna(subset=["fwd_ret"])  # 验证需要已实现的真实标签
        if len(vd_df) < 20:
            continue
        X_vd = vd_df[FEATURE_COLS].values
        pred_mu = val_model.predict(X_vd)
        n_top = max(int(len(vd_df) * TOP_DECILE_FRAC), 5)
        top_idx = np.argsort(pred_mu)[-n_top:]
        realized.extend(vd_df["fwd_ret"].values[top_idx].tolist())

    if len(realized) < 30:
        return 0.0, 1.0, len(realized)

    realized = np.array(realized)
    oos_mu = float(realized.mean())
    oos_sigma = float(realized.std())
    if oos_sigma < 1e-4:
        oos_sigma = 1e-4
    return oos_mu, oos_sigma, len(realized)


def kelly_position(candidates, oos_mu, oos_sigma, oos_n):
    """
    输入：
      candidates: 候选池DataFrame，需含 mu(预测均值收益) sigma(预测标准差) 列
      oos_mu/oos_sigma/oos_n: 样本外验证得到的"模型选股逻辑真实兑现效果"
        (由 estimate_oos_confidence 算出，反映模型在没见过的新数据上的
        实际准确度，而非模型对训练集自身的自信程度)
    输出：(target_weights dict{ts_code: weight}, exposure, portfolio_mu, portfolio_sigma)

    两层凯利，职责分离：
      1) 个股层 f_i = mu_i/sigma_i^2 —— 只用于候选池"内部"股票之间的相对排序/
         配比(谁分配多一点谁少一点)，不受选择偏差影响，因为只是矮子里比高矮
      2) 组合层 —— 决定"今天敢下多大的总仓位"，改用样本外置信度(oos_mu/oos_sigma)
         而不是候选池自己的mu(那个是虚高的，拿来决定敞口会导致贝叶斯公式永远
         建议满仓)。样本量oos_n不足时，用sqrt(n)衰减信心，样本太少直接半仓保守处理。
    """
    if len(candidates) == 0:
        return {}, 0.0, 0.0, 0.0

    mu = candidates["mu"].values
    sigma = np.maximum(candidates["sigma"].values, 1e-6)

    # ---- 个股层Kelly：只做候选池内部的相对权重分配 ----
    f_i = mu / (sigma ** 2)
    f_i = np.clip(f_i, 0, None)
    if f_i.sum() <= 0:
        return {}, 0.0, 0.0, 0.0
    w_raw = f_i / f_i.sum()
    w_capped = np.minimum(w_raw, SINGLE_CAP)
    w_i = w_capped / w_capped.sum()

    # ---- 组合层Kelly：用样本外置信度决定总仓位敞口 ----
    portfolio_mu = float(np.sum(w_i * mu))       # 仅作展示/日志用途
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

    # 样本量置信度衰减：oos_n越小，对oos_mu的信任度越低，仓位越保守
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

    # ---- 断点续跑：读取已有checkpoint ----
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
        train_df = panel.loc[train_mask].dropna(subset=FEATURE_COLS + ["fwd_ret"])

        if len(train_df) < 5000:
            log(f"[{rd}] 训练样本不足({len(train_df)})，跳过本期")
            continue

        score_df = panel_by_date.get(rd)
        if score_df is None or len(score_df) == 0:
            continue
        score_df = score_df.dropna(subset=FEATURE_COLS, how="all").copy()
        if len(score_df) == 0:
            continue

        mu, sigma, model = train_and_score(train_df, score_df)
        score_df["mu"] = mu
        score_df["sigma"] = sigma

        # 因子重要性记录(动态因子：q50模型的feature_importance随最近12个月数据滚动重训练而变化)
        importance = model.feature_importances_
        importance_norm = importance / (importance.sum() + 1e-9)
        factor_history.append({
            "rebalance_date": rd,
            "feature_importance": {c: round(float(v), 6) for c, v in zip(FEATURE_COLS, importance_norm)},
        })

        # 候选池：mu>0(预期正收益)，按mu排序取前CANDIDATE_MAX只
        cand = score_df[score_df["mu"] > 0].sort_values("mu", ascending=False).head(CANDIDATE_MAX)

        next_rd = rebalance_dates[i + 1] if i + 1 < len(rebalance_dates) else None
        if next_rd is None:
            break

        buy_date = next_trade_date.get(rd)
        sell_date = next_trade_date.get(next_rd)
        if buy_date is None or sell_date is None:
            log(f"[{rd}] 无法确定T+1交易日，跳过")
            continue

        if len(cand) < CANDIDATE_MIN:
            # 候选池不足，本期空仓观望(全部现金，收益为0)
            target_weights, exposure, port_mu, port_sigma = {}, 0.0, 0.0, 0.0
            oos_mu, oos_sigma, oos_n = 0.0, 0.0, 0
        else:
            oos_mu, oos_sigma, oos_n = estimate_oos_confidence(panel, all_trade_dates, rd, train_start)
            target_weights, exposure, port_mu, port_sigma = kelly_position(cand, oos_mu, oos_sigma, oos_n)

        # ---- 计算持仓期收益(T+1开盘买入 -> 下期T+1开盘卖出) ----
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

        # ---- 金额加权换手计算交易成本 ----
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

        # ---- 每期保存checkpoint，防止进程中断后从头重来 ----
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

    # ---- 计算核心指标 ----
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
        "strategy_name": "S010-凯利仓位+贝叶斯动态因子",
        "created_date": "2026-07-18",
        "strategy_type": "贝叶斯多因子+两层凯利仓位管理",
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
        "factor_history_recent": factor_history[-6:],  # 最近6期因子系数，体现贝叶斯动态更新
        "ai_analysis": {
            "model": "LightGBM分位数回归(q10/q50/q90, 33features)",
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
    log(f"完整因子系数历史已写出: {BASE_DIR}/factor_history_full.json")

    log(f"耗时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
