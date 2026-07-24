#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S019 实时打分 → 生成明日买入清单
用面板最新数据训练模型，对喜神池打分选Top20，输出含手数和资金的可执行清单
"""
import sqlite3, time
import numpy as np, pandas as pd
import lightgbm as lgb

PANEL_PATH = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S013-喜神池LightGBM选股/features_panel.pkl"
XISHEN_PATH = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S013-喜神池LightGBM选股/xishen_plus_pool.csv"
DB_PATH = "/Users/ziruzhu/stock-data/stock_all.db"

HORIZON = 10          # S019: 10天label
TRAIN_MONTHS = 12
TOP_N = 20
PRICE_LIMIT = 500     # 超过此股价跳过（避免1手超配）
TOTAL = 1_000_000     # 总仓（100万）
HOLD_TDAYS = 10       # 约10个交易日后卖出

FEATURE_COLS = [
    "mom_5","mom_10","mom_20","mom_60","mom_120",
    "turnover_rate","turnover_rate_f","volume_ratio","vol_chg_20",
    "bias_5","bias_10","bias_20","bias_60",
    "macd_dif","macd_dea","macd","kdj_k","kdj_d","kdj_j",
    "rsi_6","rsi_12","rsi_24","cci","boll_pct","boll_width",
    "pe","pe_ttm","pb","ps","ps_ttm","dv_ttm",
    "net_mf_ratio","lg_buy_ratio",
]

def log(msg): print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

t0 = time.time()
log("加载面板...")
panel = pd.read_pickle(PANEL_PATH)

# 确认最新日期（打分日）
all_dates = sorted(panel["trade_date"].unique())
score_date = all_dates[-1]  # 最新交易日作为打分日
log(f"打分日(面板最新): {score_date}")

# 喜神池
pool = pd.read_csv(XISHEN_PATH)
xishen_set = set(pool["ts_code"])
log(f"喜神池: {len(xishen_set)} 只")

# 黑名单：静态黑名单 + 动态 ST（stock_list.name 含 ST 的）
conn = sqlite3.connect(DB_PATH)
st_static = pd.read_sql("SELECT ts_code FROM blacklist_st", conn)["ts_code"].tolist()
loss = pd.read_sql("SELECT ts_code FROM blacklist_loss", conn)["ts_code"].tolist()
# 动态 ST：stock_list 里名字含 ST（覆盖静态快照建完后新加的 ST 股）
st_dynamic = pd.read_sql(
    "SELECT ts_code FROM stock_list WHERE name LIKE '%ST%'", conn
)["ts_code"].tolist()
blacklist = set(st_static) | set(loss) | set(st_dynamic)
log(f"黑名单: 静态ST {len(st_static)} + 动态ST {len(st_dynamic)} + 亏损 {len(loss)} = 共去重 {len(blacklist)} 只")

# 查股票名称和最新价格
score_date_dt = pd.to_datetime(score_date, format="%Y%m%d")
train_start = (score_date_dt - pd.DateOffset(months=TRAIN_MONTHS)).strftime("%Y%m%d")

# 面板清洗
panel = panel[~panel["ts_code"].isin(blacklist)].reset_index(drop=True)
panel = panel[~panel["ts_code"].str.endswith(".BJ")].reset_index(drop=True)
panel = panel.dropna(subset=FEATURE_COLS, how="all")

# 重算10天label
log("重算label(10天)...")
panel = panel.sort_values(["ts_code","trade_date"]).reset_index(drop=True)
panel["fwd_ret_new"] = panel.groupby("ts_code")["close_qfq"].transform(lambda s: s.shift(-HORIZON)/s - 1)
median_by_date = panel.groupby("trade_date")["fwd_ret_new"].transform("median")
panel["label"] = (panel["fwd_ret_new"] > median_by_date).astype("Int8")
panel.loc[panel["fwd_ret_new"].isna(), "label"] = pd.NA

# 训练集：score_date前12个月
train_mask = (panel["trade_date"] >= train_start) & (panel["trade_date"] < score_date)
train_df = panel.loc[train_mask].dropna(subset=FEATURE_COLS + ["label"])
log(f"训练集: {len(train_df):,} 行 ({train_start} ~ {score_date})")

# 打分集：喜神池 + 最新日期
score_df = panel[panel["trade_date"] == score_date].copy()
score_df = score_df[score_df["ts_code"].isin(xishen_set)]
score_df = score_df.dropna(subset=FEATURE_COLS, how="all")
log(f"打分候选(过滤前): {len(score_df)} 只")

# S019固化规则: 打分前从候选池剔除真实股价>500元的股(与回测一致)
_cph = ",".join("'" + c + "'" for c in score_df["ts_code"].tolist())
_rpx = pd.read_sql("SELECT ts_code, close FROM daily WHERE trade_date='" + score_date + "' AND ts_code IN (" + _cph + ")", conn)
_rpx["close"] = pd.to_numeric(_rpx["close"], errors="coerce")
_price_map = _rpx.set_index("ts_code")["close"]
_keep = score_df["ts_code"].map(_price_map)
_n_before = len(score_df)
score_df = score_df[(_keep <= PRICE_LIMIT) & (_keep.notna())]
log(f"打分候选(剔除>{PRICE_LIMIT}元后): {len(score_df)} 只, 剔除{_n_before-len(score_df)}只")

# 训练
log("训练LightGBM...")
model = lgb.LGBMClassifier(
    boosting_type="gbdt", num_leaves=31, learning_rate=0.05,
    n_estimators=200, subsample=0.8, colsample_bytree=0.8,
    random_state=42, verbose=-1)
model.fit(train_df[FEATURE_COLS], train_df["label"])

# 打分
scores = model.predict_proba(score_df[FEATURE_COLS])[:, 1]
score_df = score_df.copy()
score_df["score"] = scores
top_df = score_df.sort_values("score", ascending=False).head(TOP_N)[["ts_code","score","close_qfq"]].copy()
top_df["close_qfq"] = pd.to_numeric(top_df["close_qfq"], errors="coerce")

# 查名称
cph = ",".join(f"'{c}'" for c in top_df["ts_code"].tolist())
names = pd.read_sql(f"SELECT ts_code, name FROM stock_list WHERE ts_code IN ({cph})", conn)
conn.close()
top_df = top_df.merge(names, on="ts_code", how="left")
top_df["rank"] = range(1, len(top_df)+1)

# 计算下一个交易日（买入日）和卖出日
next_td = all_dates[all_dates.index(score_date)+1] if score_date in all_dates[:-1] else "未知"
# 卖出日 ≈ 买入日后HOLD_TDAYS个交易日
buy_idx = all_dates.index(next_td) if next_td in all_dates else -1
sell_td = all_dates[buy_idx + HOLD_TDAYS] if buy_idx >= 0 and buy_idx + HOLD_TDAYS < len(all_dates) else "约10交易日后"

log(f"训练完成，耗时{time.time()-t0:.0f}s")

# ─── 生成买入清单 ───
print("\n" + "="*70)
print(f"  S019 明日买入清单")
print(f"  打分日: {score_date}  买入日: {next_td}（明天开盘买入）")
print(f"  预计卖出日: {sell_td}（约{HOLD_TDAYS}个交易日后）")
print(f"  总仓: {TOTAL/1e4:.0f}万  剔除>{PRICE_LIMIT}元高价股")
print("="*70)

# 候选池已在打分前过滤掉>500元股，Top20直接就是目标持仓
buy_df = top_df.copy()
n_buy = len(buy_df)
per = TOTAL / n_buy
buy_df["lots"] = (per / (buy_df["close_qfq"]*100)).apply(lambda x: max(1, int(x)))
buy_df["actual"] = buy_df["lots"] * buy_df["close_qfq"] * 100
buy_df["dev"] = (buy_df["actual"] - per) / per * 100

total_need = buy_df["actual"].sum()

print(f"\n  买入 {n_buy} 只，每只目标 {per/1e4:.2f}万\n")
print(f"  {'排名':<4} {'代码':<12} {'名称':<10} {'参考价':>8}  {'建议手数':>8}  {'约需资金':>9}  {'偏差%':>7}")
print(f"  {'─'*70}")
for _, r in buy_df.iterrows():
    flag = "⚠️" if r["close_qfq"] > 200 else ""
    print(f"  {int(r['rank']):<4} {r['ts_code']:<12} {str(r.get('name','?'))[:8]:<10} "
          f"{r['close_qfq']:>8.2f}元  {r['lots']:>6}手  {r['actual']/1e4:>7.1f}万  "
          f"{r['dev']:>+6.1f}%  {flag}")
print(f"  {'─'*70}")
print(f"  合计资金需求: {total_need/1e4:.1f}万元  (目标{TOTAL/1e4:.0f}万，余{(TOTAL-total_need)/1e4:.1f}万可备用)")

print(f"\n  操作提示:")
print(f"  ① 明天({next_td})开盘后逐笔买入，优先成交量大的票")
print(f"  ② 参考价基于{score_date}收盘价，明天开盘价可能有差异，按当时价格调整手数")
print(f"  ③ 到{sell_td}附近开盘后卖出，不要拖延")
print(f"  ④ 当前市场处于调整期，S019近2期收益: -1.27% / +2.99% / -8.86%，注意控制风险")
