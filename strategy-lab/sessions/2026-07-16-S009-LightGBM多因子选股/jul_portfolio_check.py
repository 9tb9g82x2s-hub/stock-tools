"""
S009 v1.5 (C1·12%止损) 7月组合实盘模拟
- 调仓日：20260701（真实7/1因子，面板已补齐）
- 买入日：20260702 开盘价
- 截止日：20260717 收盘价
- 止损：C1 口径 = 每日收盘价触发，收盘价出场（而非次日开盘）
- 止损线：买入价 × 0.88
- 对比：沪深300 同期（买入日开盘→截止日收盘）
"""

import sqlite3, pandas as pd, numpy as np, lightgbm as lgb, time, json

DB = '/Users/ziruzhu/stock-data/stock_all.db'
PANEL = '/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股/features_panel.pkl'
FEATURE_COLS = [
    "mom_5","mom_10","mom_20","mom_60","mom_120",
    "turnover_rate","turnover_rate_f","volume_ratio","vol_chg_20",
    "bias_5","bias_10","bias_20","bias_60",
    "macd_dif","macd_dea","macd","kdj_k","kdj_d","kdj_j",
    "rsi_6","rsi_12","rsi_24","cci","boll_pct","boll_width",
    "pe","pe_ttm","pb","ps","ps_ttm","dv_ttm","net_mf_ratio","lg_buy_ratio"
]

REBAL_DATE  = '20260701'   # 真实7/1调仓日（面板已补齐7月因子）
BUY_DATE    = '20260702'   # 买入日（T+1开盘）
SELL_DATE   = '20260717'   # 截止日（当前最新收盘）
STOP_PCT    = 0.12         # C1 12% 止损

def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

con = sqlite3.connect(DB)
bl_st   = set(pd.read_sql("SELECT ts_code FROM blacklist_st", con)["ts_code"])
bl_loss = set(pd.read_sql("SELECT ts_code FROM blacklist_loss", con)["ts_code"])
bl = bl_st | bl_loss
sinfo = pd.read_sql("SELECT ts_code,name,industry FROM stock_list", con).set_index("ts_code")

# 读面板，训练选股
log("读面板并训练 LightGBM...")
panel = pd.read_pickle(PANEL)
panel = panel[~panel["ts_code"].isin(bl)]
panel = panel[~panel["ts_code"].str.endswith(".BJ")].reset_index(drop=True)
panel = panel.dropna(subset=FEATURE_COLS, how="all")

train_start = '20250626'
tr = panel[(panel["trade_date"] >= train_start) & (panel["trade_date"] < REBAL_DATE)].dropna(subset=FEATURE_COLS + ["label"])
sc = panel[panel["trade_date"] == REBAL_DATE].dropna(subset=FEATURE_COLS, how="all").copy()
log(f"训练 {len(tr):,} 行，评分 {len(sc)} 股")

m = lgb.LGBMClassifier(
    boosting_type="gbdt", num_leaves=31, learning_rate=0.05,
    n_estimators=200, subsample=0.8, colsample_bytree=0.8,
    random_state=42, verbose=-1
)
m.fit(tr[FEATURE_COLS], tr["label"])
sc["score"] = m.predict_proba(sc[FEATURE_COLS])[:, 1]
top20 = sc.sort_values("score", ascending=False).head(20)["ts_code"].tolist()

log(f"7月Top20选出 {len(top20)} 只")

# 获取买入日开盘价 + 持仓期每日收盘价（用于逐日止损检查）
codes_str = ','.join([f"'{c}'" for c in top20])

# 获取 BUY_DATE 的开盘价
df_buy = pd.read_sql(
    f"SELECT ts_code, open FROM daily WHERE ts_code IN ({codes_str}) AND trade_date='{BUY_DATE}'",
    con
).set_index("ts_code")
df_buy["open"] = df_buy["open"].astype(float)

# 获取 BUY_DATE 到 SELL_DATE 的每日收盘价（用于止损检查）
df_daily = pd.read_sql(
    f"""SELECT ts_code, trade_date, close FROM daily
        WHERE ts_code IN ({codes_str})
        AND trade_date >= '{BUY_DATE}' AND trade_date <= '{SELL_DATE}'
        ORDER BY ts_code, trade_date""",
    con
)
df_daily["close"] = df_daily["close"].astype(float)

# 获取持仓期所有交易日（用来确认日期序列）
all_dates = sorted(df_daily["trade_date"].unique())
log(f"持仓期交易日: {all_dates}")

# 计算每只票的收益（带止损）
rows = []
for code in top20:
    nm  = sinfo.loc[code, "name"]    if code in sinfo.index else code
    ind = sinfo.loc[code, "industry"] if code in sinfo.index else ""

    if code not in df_buy.index:
        log(f"  {nm}({code}) 买入日无数据，跳过")
        continue

    p_buy = float(df_buy.loc[code, "open"])
    if p_buy <= 0 or pd.isna(p_buy):
        continue

    stop_price = round(p_buy * (1 - STOP_PCT), 3)

    # 逐日检查止损（C1：收盘价触发，收盘价出场）
    closes = df_daily[df_daily["ts_code"] == code].sort_values("trade_date")
    triggered = False
    exit_price = None
    exit_date  = None

    for _, row in closes.iterrows():
        c = row["close"]
        d = row["trade_date"]
        if pd.isna(c): continue
        if d == BUY_DATE:
            # 买入日不计止损（当天刚买）
            continue
        if c <= stop_price:
            triggered = True
            exit_price = c
            exit_date  = d
            break

    if not triggered:
        # 未触发止损，用最终 SELL_DATE 收盘价
        last_close = closes[closes["trade_date"] == SELL_DATE]
        if last_close.empty:
            last_close = closes.iloc[-1:]
        exit_price = float(last_close.iloc[0]["close"])
        exit_date  = last_close.iloc[0]["trade_date"]

    r_pct = round((exit_price / p_buy - 1) * 100, 2)

    rows.append({
        "code":        code,
        "name":        nm,
        "industry":    ind,
        "score":       round(float(sc[sc["ts_code"] == code]["score"].values[0]), 4),
        "buy_price":   p_buy,
        "stop_price":  stop_price,
        "exit_price":  exit_price,
        "exit_date":   exit_date,
        "triggered":   triggered,
        "r_pct":       r_pct
    })

res = pd.DataFrame(rows)

# 同期沪深300代理（用沪深300成分股等权，或全市场等权）
mkt_buy  = pd.read_sql(f"SELECT ts_code, open FROM daily WHERE trade_date='{BUY_DATE}'", con)
mkt_sell = pd.read_sql(f"SELECT ts_code, close FROM daily WHERE trade_date='{SELL_DATE}'", con)
mkt_buy["open"]   = mkt_buy["open"].astype(float)
mkt_sell["close"] = mkt_sell["close"].astype(float)
mg = mkt_buy.merge(mkt_sell, on="ts_code")
mg = mg[(mg["open"] > 0) & (mg["close"] > 0)]
mg["r"] = (mg["close"] / mg["open"] - 1) * 100
mkt_median = float(mg["r"].median())
mkt_mean   = float(mg["r"].mean())

con.close()

# ======================== 输出 ========================
port = float(res["r_pct"].mean())
win  = int((res["r_pct"] > 0).sum())
stop_count = int(res["triggered"].sum())

print()
print("=" * 65)
print(f"【S009 v1.5 · 7月组合实盘模拟】")
print(f"  基于6/26因子选股，近似7/1调仓，7/2开盘买入 → 7/17收盘")
print(f"  止损：C1口径 12%（收盘价触发）")
print()
print(f"  📊 组合收益:       {port:+.2f}%  ({win}/{len(res)} 盈利，{stop_count} 只触发止损)")
print(f"  📊 全市场中位数:   {mkt_median:+.2f}%")
print(f"  📊 全市场均值:     {mkt_mean:+.2f}%")
print(f"  📊 超额(vs中位数): {port - mkt_median:+.2f}%")
print()
print(f"  持仓期: {BUY_DATE} → {SELL_DATE}  ({len(all_dates)} 个交易日)")
print()

print(f"{'股票':<9}{'行业':<9}{'买入':>7}{'止损线':>7}{'出场价':>7}{'收益':>8} {'状态'}")
print("-" * 65)
for _, r in res.sort_values("r_pct", ascending=False).iterrows():
    status = "⚡止损" if r["triggered"] else "✅持仓"
    flag   = "🔴" if r["r_pct"] < -5 else ("🟡" if r["r_pct"] < 0 else "🟢")
    print(f"{r['name']:<9}{r['industry']:<9}{r['buy_price']:>7.2f}{r['stop_price']:>7.2f}{r['exit_price']:>7.2f}{r['r_pct']:>+7.1f}%  {status} {flag}")

print()
print(f"行业分布: {res['industry'].value_counts().to_dict()}")

# 保存 JSON 供面板用
out = {
    "portfolio_return": round(port, 2),
    "market_median": round(mkt_median, 2),
    "market_mean": round(mkt_mean, 2),
    "excess_return": round(port - mkt_median, 2),
    "win_count": win,
    "total_count": len(res),
    "stop_count": stop_count,
    "buy_date": BUY_DATE,
    "sell_date": SELL_DATE,
    "holdings": rows
}
json.dump(out, open('/Users/ziruzhu/WorkBuddy/2026-07-16-19-25-41/jul_portfolio_result.json', 'w'), ensure_ascii=False, indent=2)
log("结果已保存到 jul_portfolio_result.json")
