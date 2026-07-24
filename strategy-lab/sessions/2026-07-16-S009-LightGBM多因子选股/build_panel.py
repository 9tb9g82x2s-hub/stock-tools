#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S009-LightGBM多因子选股 · 特征面板构建脚本

从 stock_all.db 读取 stk_factor / daily_basic / moneyflow 三张表，
按 ts_code 分组计算 33 个横截面特征（动量5+量能4+均线偏离4+技术指标12+估值6+资金流2），
并构造未来10个交易日相对全市场中位数收益的二分类标签。

产物：features_panel.parquet（全量日频特征面板，供 train_backtest.py 使用）
"""
import sqlite3
import time
import numpy as np
import pandas as pd

DB_PATH = "/Users/ziruzhu/stock-data/stock_all.db"
OUT_PATH = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股/features_panel.pkl"
START_DATE = "20160101"  # 从2016年开始，为2017年起的回测预留12个月+120日动量的历史窗口
LABEL_HORIZON = 10  # 未来10个交易日标签窗口


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_stk_factor(con):
    log("读取 stk_factor ...")
    cols = [
        "ts_code", "trade_date", "close_qfq", "open_qfq", "vol",
        "macd_dif", "macd_dea", "macd",
        "kdj_k", "kdj_d", "kdj_j",
        "rsi_6", "rsi_12", "rsi_24",
        "boll_upper", "boll_mid", "boll_lower", "cci",
    ]
    sql = f"SELECT {','.join(cols)} FROM stk_factor WHERE trade_date >= '{START_DATE}'"
    df = pd.read_sql(sql, con)
    for c in cols[2:]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float32")
    log(f"stk_factor 读取完成: {len(df):,} 行")
    return df


def load_daily_basic(con):
    log("读取 daily_basic ...")
    cols = [
        "ts_code", "trade_date", "turnover_rate", "turnover_rate_f",
        "volume_ratio", "pe", "pe_ttm", "pb", "ps", "ps_ttm", "dv_ttm",
    ]
    sql = f"SELECT {','.join(cols)} FROM daily_basic WHERE trade_date >= '{START_DATE}'"
    df = pd.read_sql(sql, con)
    for c in cols[2:]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float32")
    log(f"daily_basic 读取完成: {len(df):,} 行")
    return df


def load_moneyflow(con):
    log("读取 moneyflow ...")
    cols = [
        "ts_code", "trade_date",
        "buy_sm_amount", "buy_md_amount", "buy_lg_amount", "buy_elg_amount",
        "sell_sm_amount", "sell_md_amount", "sell_lg_amount", "sell_elg_amount",
        "net_mf_amount",
    ]
    sql = f"SELECT {','.join(cols)} FROM moneyflow WHERE trade_date >= '{START_DATE}'"
    df = pd.read_sql(sql, con)
    for c in cols[2:]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float32")
    log(f"moneyflow 读取完成: {len(df):,} 行")
    return df


def compute_features(df):
    """按 ts_code 分组计算滚动特征，df 需已按 ts_code, trade_date 排序"""
    g = df.groupby("ts_code", sort=False)

    close = df["close_qfq"]

    log("计算动量特征 (5个)...")
    for w in [5, 10, 20, 60, 120]:
        df[f"mom_{w}"] = g["close_qfq"].transform(lambda s, w=w: s / s.shift(w) - 1)

    log("计算均线偏离特征 (4个)...")
    for w in [5, 10, 20, 60]:
        ma = g["close_qfq"].transform(lambda s, w=w: s.rolling(w).mean())
        df[f"bias_{w}"] = close / ma - 1

    log("计算量能特征 (4个)...")
    df["vol_chg_20"] = g["vol"].transform(
        lambda s: s / s.rolling(20).mean() - 1
    )
    # turnover_rate / turnover_rate_f / volume_ratio 直接使用 daily_basic 原始字段，此处占位在 merge 后已存在

    log("计算布林带位置/宽度 (技术指标补充)...")
    df["boll_pct"] = (close - df["boll_lower"]) / (df["boll_upper"] - df["boll_lower"] + 1e-6)
    df["boll_width"] = (df["boll_upper"] - df["boll_lower"]) / (df["boll_mid"] + 1e-6)

    log("计算资金流特征 (2个)...")
    total_buy = (
        df["buy_sm_amount"] + df["buy_md_amount"] + df["buy_lg_amount"] + df["buy_elg_amount"]
    )
    total_sell = (
        df["sell_sm_amount"] + df["sell_md_amount"] + df["sell_lg_amount"] + df["sell_elg_amount"]
    )
    total_amt = total_buy + total_sell
    df["net_mf_ratio"] = df["net_mf_amount"] / (total_amt + 1e-6)
    df["lg_buy_ratio"] = (df["buy_lg_amount"] + df["buy_elg_amount"]) / (total_buy + 1e-6)

    return df


def compute_forward_label(df, horizon=LABEL_HORIZON):
    """未来 horizon 个交易日收益，以及是否跑赢当日全市场中位数收益的二分类标签"""
    log(f"计算未来{horizon}日标签...")
    g = df.groupby("ts_code", sort=False)
    df["fwd_ret"] = g["close_qfq"].transform(
        lambda s: s.shift(-horizon) / s - 1
    )
    # 横截面中位数（按 trade_date 分组）
    median_by_date = df.groupby("trade_date")["fwd_ret"].transform("median")
    df["label"] = (df["fwd_ret"] > median_by_date).astype("Int8")
    return df


def main():
    t0 = time.time()
    con = sqlite3.connect(DB_PATH)

    sf = load_stk_factor(con)
    db = load_daily_basic(con)
    mf = load_moneyflow(con)
    con.close()

    log("合并三张表...")
    df = sf.merge(db, on=["ts_code", "trade_date"], how="inner")
    df = df.merge(mf, on=["ts_code", "trade_date"], how="left")
    log(f"合并完成: {len(df):,} 行, {df['ts_code'].nunique():,} 只股票")

    log("排序...")
    df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)

    df = compute_features(df)
    df = compute_forward_label(df)

    feature_cols = [
        "mom_5", "mom_10", "mom_20", "mom_60", "mom_120",
        "turnover_rate", "turnover_rate_f", "volume_ratio", "vol_chg_20",
        "bias_5", "bias_10", "bias_20", "bias_60",
        "macd_dif", "macd_dea", "macd", "kdj_k", "kdj_d", "kdj_j",
        "rsi_6", "rsi_12", "rsi_24", "cci", "boll_pct", "boll_width",
        "pe", "pe_ttm", "pb", "ps", "ps_ttm", "dv_ttm",
        "net_mf_ratio", "lg_buy_ratio",
    ]
    log(f"特征总数: {len(feature_cols)}")

    keep_cols = ["ts_code", "trade_date", "close_qfq", "open_qfq"] + feature_cols + ["fwd_ret", "label"]
    df = df[keep_cols]

    # 精简内存：下调 float64 -> float32（fwd_ret、close_qfq、open_qfq保留）
    for c in feature_cols + ["fwd_ret", "close_qfq", "open_qfq"]:
        df[c] = df[c].astype("float32")

    log(f"写出 pickle: {OUT_PATH}")
    df.to_pickle(OUT_PATH)

    log(f"完成，总耗时 {time.time()-t0:.1f}s，输出 {len(df):,} 行 x {len(df.columns)} 列")


if __name__ == "__main__":
    main()
