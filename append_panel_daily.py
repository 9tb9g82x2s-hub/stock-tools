#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
通用日更：自动检测面板最晚日期，追加新行到 features_panel.pkl
自动计算 APPEND_FROM = 面板最晚日 + 1，幂等可重复执行
"""
import sqlite3, time, shutil, os
import numpy as np
import pandas as pd

DB_PATH = "/Users/ziruzhu/stock-data/stock_all.db"
PANEL = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股/features_panel.pkl"
READ_START = "20250101"   # 只读近1年半，够120日动量+滚动窗口
# APPEND_FROM 在 main() 里动态计算
LABEL_HORIZON = 10

def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

def load_stk_factor(con):
    log("读取 stk_factor (2025至今)...")
    cols = ["ts_code","trade_date","close_qfq","open_qfq","vol",
            "macd_dif","macd_dea","macd","kdj_k","kdj_d","kdj_j",
            "rsi_6","rsi_12","rsi_24","boll_upper","boll_mid","boll_lower","cci"]
    df = pd.read_sql(f"SELECT {','.join(cols)} FROM stk_factor WHERE trade_date >= '{READ_START}'", con)
    for c in cols[2:]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float32")
    log(f"stk_factor: {len(df):,} 行")
    return df

def load_daily_basic(con):
    log("读取 daily_basic (2025至今)...")
    cols = ["ts_code","trade_date","turnover_rate","turnover_rate_f",
            "volume_ratio","pe","pe_ttm","pb","ps","ps_ttm","dv_ttm"]
    df = pd.read_sql(f"SELECT {','.join(cols)} FROM daily_basic WHERE trade_date >= '{READ_START}'", con)
    for c in cols[2:]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float32")
    log(f"daily_basic: {len(df):,} 行")
    return df

def load_moneyflow(con):
    log("读取 moneyflow (2025至今)...")
    cols = ["ts_code","trade_date","buy_sm_amount","buy_md_amount","buy_lg_amount","buy_elg_amount",
            "sell_sm_amount","sell_md_amount","sell_lg_amount","sell_elg_amount","net_mf_amount"]
    df = pd.read_sql(f"SELECT {','.join(cols)} FROM moneyflow WHERE trade_date >= '{READ_START}'", con)
    for c in cols[2:]:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float32")
    log(f"moneyflow: {len(df):,} 行")
    return df

def compute_features(df):
    g = df.groupby("ts_code", sort=False)
    close = df["close_qfq"]
    log("动量5...")
    for w in [5,10,20,60,120]:
        df[f"mom_{w}"] = g["close_qfq"].transform(lambda s, w=w: s/s.shift(w)-1)
    log("均线偏离4...")
    for w in [5,10,20,60]:
        ma = g["close_qfq"].transform(lambda s, w=w: s.rolling(w).mean())
        df[f"bias_{w}"] = close/ma-1
    log("量能...")
    df["vol_chg_20"] = g["vol"].transform(lambda s: s/s.rolling(20).mean()-1)
    log("布林...")
    df["boll_pct"] = (close-df["boll_lower"])/(df["boll_upper"]-df["boll_lower"]+1e-6)
    df["boll_width"] = (df["boll_upper"]-df["boll_lower"])/(df["boll_mid"]+1e-6)
    log("资金流2...")
    total_buy = df["buy_sm_amount"]+df["buy_md_amount"]+df["buy_lg_amount"]+df["buy_elg_amount"]
    total_sell = df["sell_sm_amount"]+df["sell_md_amount"]+df["sell_lg_amount"]+df["sell_elg_amount"]
    total_amt = total_buy+total_sell
    df["net_mf_ratio"] = df["net_mf_amount"]/(total_amt+1e-6)
    df["lg_buy_ratio"] = (df["buy_lg_amount"]+df["buy_elg_amount"])/(total_buy+1e-6)
    return df

def compute_label(df, horizon=LABEL_HORIZON):
    log(f"未来{horizon}日标签...")
    g = df.groupby("ts_code", sort=False)
    df["fwd_ret"] = g["close_qfq"].transform(lambda s: s.shift(-horizon)/s-1)
    med = df.groupby("trade_date")["fwd_ret"].transform("median")
    df["label"] = (df["fwd_ret"]>med).astype("Int8")
    return df

def main():
    t0 = time.time()
    # 动态计算：从面板最晚日的下一个交易日开始追加
    import sqlite3 as _sq3, pandas as _pd
    _old_peek = _pd.read_pickle(PANEL)
    _latest = _old_peek["trade_date"].max()
    del _old_peek
    APPEND_FROM = _latest  # 当天会被重新算并覆盖，保证幂等
    log(f"面板最晚日: {_latest}, 将追加 >= {APPEND_FROM} 的新行")
    con = sqlite3.connect(DB_PATH)
    sf = load_stk_factor(con); db = load_daily_basic(con); mf = load_moneyflow(con)
    con.close()

    log("合并三表...")
    df = sf.merge(db, on=["ts_code","trade_date"], how="inner")
    df = df.merge(mf, on=["ts_code","trade_date"], how="left")
    log(f"合并: {len(df):,} 行, {df['ts_code'].nunique():,} 股")

    df = df.sort_values(["ts_code","trade_date"]).reset_index(drop=True)
    df = compute_features(df)
    df = compute_label(df)

    feature_cols = ["mom_5","mom_10","mom_20","mom_60","mom_120",
        "turnover_rate","turnover_rate_f","volume_ratio","vol_chg_20",
        "bias_5","bias_10","bias_20","bias_60",
        "macd_dif","macd_dea","macd","kdj_k","kdj_d","kdj_j",
        "rsi_6","rsi_12","rsi_24","cci","boll_pct","boll_width",
        "pe","pe_ttm","pb","ps","ps_ttm","dv_ttm","net_mf_ratio","lg_buy_ratio"]
    keep = ["ts_code","trade_date","close_qfq","open_qfq"]+feature_cols+["fwd_ret","label"]
    df = df[keep]
    for c in feature_cols+["fwd_ret","close_qfq","open_qfq"]:
        df[c] = df[c].astype("float32")

    # 只保留要追加的新行
    new_rows = df[df["trade_date"] >= APPEND_FROM].copy()
    log(f"新行(>={APPEND_FROM}): {len(new_rows):,} 行, 交易日: {sorted(new_rows['trade_date'].unique())}")

    # 读现有面板，备份，追加
    log("读现有面板...")
    old = pd.read_pickle(PANEL)
    log(f"现有面板: {len(old):,} 行, 最晚 {old['trade_date'].max()}")

    # 防重复：若现有面板已含>=APPEND_FROM的行则先剔除(理论上没有)
    old_clean = old[old["trade_date"] < APPEND_FROM]
    if len(old_clean) < len(old):
        log(f"警告: 原面板含{len(old)-len(old_clean)}行>={APPEND_FROM}的旧数据,已剔除避免重复")

    log("备份原面板...")
    bak = PANEL + ".bak_" + time.strftime("%Y%m%d_%H%M%S")
    shutil.copy2(PANEL, bak)
    log(f"备份到: {bak}")

    merged = pd.concat([old_clean, new_rows], ignore_index=True)
    merged = merged.sort_values(["ts_code","trade_date"]).reset_index(drop=True)
    log(f"合并后: {len(merged):,} 行, 最晚 {merged['trade_date'].max()}")

    log("写出...")
    merged.to_pickle(PANEL)
    log(f"完成! 总耗时 {time.time()-t0:.1f}s")

    # 验证
    ds = sorted(merged["trade_date"].unique())
    log(f"最终面板: 最早{ds[0]} 最晚{ds[-1]} 交易日{len(ds)}")
    recent = [d for d in ds if d >= APPEND_FROM]
    log(f"本次追加交易日({len(recent)}个): {recent[:5]}...{recent[-1] if len(recent)>5 else ''}")

if __name__ == "__main__":
    main()
