#!/usr/bin/env python3
"""
LightGBM量化回测 - 滚动训练 + 组合回测（S009-LightGBM多因子选股）

方法：
  1. 读取 build_features.py 生成的 feature_cache.parquet
  2. 按月滚动：用过去N个月数据训练LightGBM二分类模型（预测未来10日是否跑赢全市场中位数）
  3. 每月月初用模型给全市场打分，选Top-K只股票等权持仓，持有到下个调仓日
  4. 统计组合净值曲线、年化收益、胜率、最大回撤、夏普比，并与全市场等权基准对比
  5. 输出 results.json（符合strategy-lab schema）+ 净值曲线csv

用法：
  /Users/ziruzhu/stock-tools/.venv/bin/python train_backtest.py
"""
import pandas as pd
import numpy as np
import lightgbm as lgb
import json
import time
from pathlib import Path

FEATURE_PATH = '/Users/ziruzhu/stock-tools/ml_backtest/feature_cache.pkl'
OUT_DIR = Path('/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股')
TRAIN_WINDOW_MONTHS = 12   # 滚动训练窗口：过去12个月
TOP_K = 20                 # 每期持仓只数
REBALANCE = 'MS'           # 每月初调仓
HOLD_DAYS = 10             # 需与build_features.py一致

LGB_PARAMS = dict(
    objective='binary',
    metric='auc',
    boosting_type='gbdt',
    num_leaves=31,
    learning_rate=0.05,
    feature_fraction=0.8,
    bagging_fraction=0.8,
    bagging_freq=5,
    min_child_samples=100,
    verbose=-1,
    n_jobs=8,
)
NUM_ROUNDS = 200

t0 = time.time()
print('读取特征缓存 ...')
df = pd.read_pickle(FEATURE_PATH)
df['trade_date'] = pd.to_datetime(df['trade_date'])
df.sort_values(['trade_date', 'ts_code'], inplace=True)

feature_cols = [c for c in df.columns if c not in
                ('ts_code', 'trade_date', 'close_qfq', 'fwd_ret', 'label', 'fwd_ret_median')]
print(f'样本={len(df)}, 特征数={len(feature_cols)}, 日期范围={df.trade_date.min().date()}~{df.trade_date.max().date()}')

all_dates = sorted(df['trade_date'].unique())
rebal_dates = pd.date_range(all_dates[0], all_dates[-1], freq=REBALANCE)
rebal_dates = [d for d in rebal_dates if d >= all_dates[0] + pd.DateOffset(months=TRAIN_WINDOW_MONTHS)]

print(f'调仓点数量: {len(rebal_dates)} (每月初, 训练窗口{TRAIN_WINDOW_MONTHS}个月)')

nav = 1.0
nav_curve = []
bench_nav = 1.0
bench_curve = []
trade_log = []
period_returns = []
feature_importance_accum = np.zeros(len(feature_cols))
model_count = 0

for i, rd in enumerate(rebal_dates):
    train_end = rd - pd.Timedelta(days=HOLD_DAYS + 1)
    train_start = train_end - pd.DateOffset(months=TRAIN_WINDOW_MONTHS)
    train_df = df[(df['trade_date'] >= train_start) & (df['trade_date'] < train_end)]
    if len(train_df) < 5000:
        continue

    trade_dates_after = [d for d in all_dates if d >= rd]
    if not trade_dates_after:
        break
    actual_signal_date = trade_dates_after[0]
    signal_df = df[df['trade_date'] == actual_signal_date]
    if signal_df.empty:
        continue

    X_train = train_df[feature_cols]
    y_train = train_df['label'].astype(int)
    train_set = lgb.Dataset(X_train, label=y_train)
    model = lgb.train(LGB_PARAMS, train_set, num_boost_round=NUM_ROUNDS)
    feature_importance_accum += model.feature_importance(importance_type='gain')
    model_count += 1

    X_signal = signal_df[feature_cols]
    scores = model.predict(X_signal)
    signal_df = signal_df.copy()
    signal_df['score'] = scores
    picks = signal_df.sort_values('score', ascending=False).head(TOP_K)

    period_ret = picks['fwd_ret'].mean()
    bench_ret = signal_df['fwd_ret'].median()

    nav *= (1 + period_ret)
    bench_nav *= (1 + bench_ret)
    nav_curve.append({'date': actual_signal_date, 'nav': nav, 'period_ret': period_ret})
    bench_curve.append({'date': actual_signal_date, 'nav': bench_nav, 'period_ret': bench_ret})
    period_returns.append(period_ret)

    for _, row in picks.iterrows():
        trade_log.append({
            'signal_date': actual_signal_date.strftime('%Y-%m-%d'),
            'ts_code': row['ts_code'],
            'score': round(float(row['score']), 4),
            'fwd_ret': round(float(row['fwd_ret']), 4),
        })

    if (i + 1) % 6 == 0 or i == len(rebal_dates) - 1:
        print(f'[{i+1}/{len(rebal_dates)}] {actual_signal_date.date()} 训练{len(train_df)}样本 '
              f'组合收益{period_ret:+.2%} 基准{bench_ret:+.2%} 累计净值{nav:.3f}')

print(f'\n训练回测完成, 共{model_count}期, 耗时{time.time()-t0:.0f}s')

nav_df = pd.DataFrame(nav_curve)
bench_df = pd.DataFrame(bench_curve)
returns = nav_df['period_ret'].values

n_periods = len(returns)
years = n_periods / 12  # 月度调仓近似
total_return = nav - 1
annual_return = (nav ** (1 / years) - 1) if years > 0 else np.nan
bench_total_return = bench_nav - 1
win_rate = float((returns > 0).mean())

cum = np.cumprod(1 + returns)
running_max = np.maximum.accumulate(cum)
drawdown = cum / running_max - 1
max_drawdown = float(drawdown.min())

if returns.std() > 0:
    sharpe = float(returns.mean() / returns.std() * np.sqrt(12))
else:
    sharpe = 0.0

wins = returns[returns > 0]
losses = returns[returns < 0]
profit_loss_ratio = float(wins.mean() / abs(losses.mean())) if len(losses) > 0 and len(wins) > 0 else None

max_consec_loss = 0
cur_streak = 0
for r in returns:
    if r < 0:
        cur_streak += 1
        max_consec_loss = max(max_consec_loss, cur_streak)
    else:
        cur_streak = 0

print(f'\n{"="*60}')
print(f'总收益: {total_return:+.1%}  (基准等权中位数: {bench_total_return:+.1%})')
print(f'年化收益: {annual_return:+.1%}')
print(f'胜率(期): {win_rate:.1%}  共{n_periods}期')
print(f'最大回撤: {max_drawdown:.1%}')
print(f'夏普比率: {sharpe:.2f}')
print(f'盈亏比: {profit_loss_ratio}')
print(f'最大连续亏损期数: {max_consec_loss}')
print(f'{"="*60}')

fi = pd.Series(feature_importance_accum / max(model_count, 1), index=feature_cols).sort_values(ascending=False)
print('\nTop15特征重要性(gain, 平均每期):')
print(fi.head(15).to_string())

OUT_DIR.mkdir(parents=True, exist_ok=True)
nav_df.to_csv(OUT_DIR / 'nav_curve.csv', index=False)
bench_df.to_csv(OUT_DIR / 'bench_curve.csv', index=False)
pd.DataFrame(trade_log).to_csv(OUT_DIR / 'trade_log.csv', index=False)
fi.to_csv(OUT_DIR / 'feature_importance.csv', header=['gain'])

last_picks = pd.DataFrame(trade_log)
last_signal_date = last_picks['signal_date'].max() if len(last_picks) else None
latest_stocks = []
if last_signal_date:
    latest = last_picks[last_picks['signal_date'] == last_signal_date].sort_values('score', ascending=False)
    stock_names = {}
    try:
        import sqlite3
        conn = sqlite3.connect('/Users/ziruzhu/stock-data/stock_all.db')
        cur = conn.cursor()
        cur.execute('SELECT ts_code, name FROM stock_list')
        stock_names = dict(cur.fetchall())
        conn.close()
    except Exception:
        pass
    for _, r in latest.head(10).iterrows():
        latest_stocks.append({
            'code': r['ts_code'],
            'name': stock_names.get(r['ts_code'], ''),
            'signal_date': last_signal_date,
            'expected_return': round(float(r['score']), 4)
        })

results = {
    "strategy_name": "S009-LightGBM多因子选股",
    "created_date": "2026-07-16",
    "strategy_type": "多因子",
    "metrics": {
        "total_return": round(float(total_return), 4),
        "annual_return": round(float(annual_return), 4) if not np.isnan(annual_return) else None,
        "win_rate": round(win_rate, 4),
        "max_drawdown": round(max_drawdown, 4),
        "sharpe_ratio": round(sharpe, 2),
        "total_trades": int(len(trade_log)),
    },
    "aux_metrics": {
        "avg_hold_days": HOLD_DAYS,
        "profit_loss_ratio": round(profit_loss_ratio, 2) if profit_loss_ratio else None,
        "max_consecutive_losses": int(max_consec_loss),
        "rebalance_periods": n_periods,
        "top_k_per_period": TOP_K,
        "train_window_months": TRAIN_WINDOW_MONTHS,
        "benchmark_total_return": round(float(bench_total_return), 4),
        "excess_return": round(float(total_return - bench_total_return), 4),
    },
    "stocks": latest_stocks,
    "ai_analysis": {
        "model": "LightGBM (gbdt, 200轮, 12个月滚动训练)",
        "summary": f"月度调仓, Top{TOP_K}等权持仓, {HOLD_DAYS}日持有期标签。"
                   f"{n_periods}期回测年化{annual_return:.1%}, 跑赢基准{total_return-bench_total_return:+.1%}。"
                   f"最重要特征: {', '.join(fi.head(5).index.tolist())}。",
        "confidence": "中"
    },
    "notes": f"训练窗口{TRAIN_WINDOW_MONTHS}个月滚动, 特征来自daily+stk_factor+daily_basic+moneyflow, "
             f"标签=未来{HOLD_DAYS}日收益是否跑赢全市场中位数(横截面二分类)。"
             f"回测区间{nav_df['date'].min().date()}~{nav_df['date'].max().date()}。",
    "status": "tested"
}

with open(OUT_DIR / 'results.json', 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print(f'\n结果已保存到: {OUT_DIR}')
print(f'  - results.json (策略仪表板格式)')
print(f'  - nav_curve.csv / bench_curve.csv (净值曲线)')
print(f'  - trade_log.csv (逐期持仓明细)')
print(f'  - feature_importance.csv (特征重要性)')
