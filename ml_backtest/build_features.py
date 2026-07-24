#!/usr/bin/env python3
"""
LightGBM量化回测 - 特征工程

从stock_all.db读取daily/stk_factor/daily_basic/moneyflow，
构造技术面+估值面+资金面特征，标签为"未来N天收益是否跑赢全市场中位数"。

输出：feature_cache.pkl（缓存，避免每次回测重新算特征。用pickle而非parquet，
      免去额外安装pyarrow的网络等待）

用法：
  /Users/ziruzhu/stock-tools/.venv/bin/python build_features.py
"""
import sqlite3
import pandas as pd
import numpy as np
import time

DB = '/Users/ziruzhu/stock-data/stock_all.db'
START_DATE = '20160101'   # stk_factor已补齐2016年起数据(见download_stkfactor_2016_2019.py)
HOLD_DAYS = 10             # 标签窗口：未来10个交易日收益
OUT_PATH = '/Users/ziruzhu/stock-tools/ml_backtest/feature_cache.pkl'

t0 = time.time()
conn = sqlite3.connect(DB)

print('读取 daily ...')
daily = pd.read_sql(f"""
    SELECT ts_code, trade_date, open, high, low, close, vol, amount
    FROM daily WHERE trade_date >= '{START_DATE}'
""", conn)

print('读取 stk_factor（技术指标+复权价）...')
factor = pd.read_sql(f"""
    SELECT ts_code, trade_date, close_qfq, adj_factor,
           macd_dif, macd_dea, macd, kdj_k, kdj_d, kdj_j,
           rsi_6, rsi_12, rsi_24, boll_upper, boll_mid, boll_lower, cci
    FROM stk_factor WHERE trade_date >= '{START_DATE}'
""", conn)

print('读取 daily_basic（估值+换手）...')
basic = pd.read_sql(f"""
    SELECT ts_code, trade_date, turnover_rate, turnover_rate_f, volume_ratio,
           pe_ttm, pb, ps_ttm, dv_ttm, total_mv, circ_mv
    FROM daily_basic WHERE trade_date >= '{START_DATE}'
""", conn)

print('读取 moneyflow（资金流向）...')
mf = pd.read_sql(f"""
    SELECT ts_code, trade_date, buy_lg_amount, sell_lg_amount,
           buy_elg_amount, sell_elg_amount, net_mf_amount
    FROM moneyflow WHERE trade_date >= '{START_DATE}'
""", conn)

print('读取 blacklist（ST/亏损股，训练时剔除）...')
cur = conn.cursor()
cur.execute('SELECT ts_code FROM blacklist_st UNION SELECT ts_code FROM blacklist_loss')
blacklist = set(r[0] for r in cur.fetchall())
conn.close()

print(f'原始数据: daily={len(daily)} factor={len(factor)} basic={len(basic)} mf={len(mf)}  耗时{time.time()-t0:.0f}s')

for df in (daily, factor, basic, mf):
    df.drop_duplicates(subset=['ts_code', 'trade_date'], inplace=True)

df = daily.merge(factor, on=['ts_code', 'trade_date'], how='inner')
df = df.merge(basic, on=['ts_code', 'trade_date'], how='left')
df = df.merge(mf, on=['ts_code', 'trade_date'], how='left')
df = df[~df['ts_code'].isin(blacklist)]

print('强制转换数值列类型（数据库里部分批次混入了字符串类型，需统一转float）...')
numeric_cols = [c for c in df.columns if c not in ('ts_code', 'trade_date')]
for c in numeric_cols:
    df[c] = pd.to_numeric(df[c], errors='coerce')

df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
df.sort_values(['ts_code', 'trade_date'], inplace=True)
df.reset_index(drop=True, inplace=True)
print(f'合并去重后: {len(df)} 行, {df["ts_code"].nunique()} 只股票')

def add_features(g):
    g = g.copy()
    c = g['close_qfq']
    g['ret_1d'] = c.pct_change(1)
    g['ret_5d'] = c.pct_change(5)
    g['ret_10d'] = c.pct_change(10)
    g['ret_20d'] = c.pct_change(20)
    g['ret_60d'] = c.pct_change(60)
    g['vol_ratio_5_20'] = g['vol'].rolling(5).mean() / g['vol'].rolling(20).mean()
    g['turnover_ma5'] = g['turnover_rate_f'].rolling(5).mean()
    ma5 = c.rolling(5).mean()
    ma20 = c.rolling(20).mean()
    ma60 = c.rolling(60).mean()
    g['ma5_gap'] = c / ma5 - 1
    g['ma20_gap'] = c / ma20 - 1
    g['ma60_gap'] = c / ma60 - 1
    g['ma5_ma20_gap'] = ma5 / ma20 - 1
    g['volatility_20d'] = g['ret_1d'].rolling(20).std()
    net_mf = g['net_mf_amount'].fillna(0)
    g['net_mf_5d'] = net_mf.rolling(5).sum()
    g['net_mf_20d'] = net_mf.rolling(20).sum()
    g['fwd_ret'] = c.shift(-HOLD_DAYS) / c - 1
    return g

print('按股票分组计算特征（动量/量比/均线偏离/波动率/资金流）...')
t1 = time.time()
df = df.groupby('ts_code', group_keys=False).apply(add_features)
print(f'特征计算完成, 耗时{time.time()-t1:.0f}s')

print('计算逐日横截面标签（是否跑赢当日全市场中位数收益）...')
df['fwd_ret_median'] = df.groupby('trade_date')['fwd_ret'].transform('median')
df['label'] = (df['fwd_ret'] > df['fwd_ret_median']).astype('Int64')

feature_cols = [
    'ret_1d', 'ret_5d', 'ret_10d', 'ret_20d', 'ret_60d',
    'vol_ratio_5_20', 'turnover_rate', 'turnover_rate_f', 'turnover_ma5', 'volume_ratio',
    'ma5_gap', 'ma20_gap', 'ma60_gap', 'ma5_ma20_gap', 'volatility_20d',
    'macd_dif', 'macd_dea', 'macd', 'kdj_k', 'kdj_d', 'kdj_j',
    'rsi_6', 'rsi_12', 'rsi_24', 'cci',
    'pe_ttm', 'pb', 'ps_ttm', 'dv_ttm', 'total_mv', 'circ_mv',
    'net_mf_5d', 'net_mf_20d',
]
keep_cols = ['ts_code', 'trade_date', 'close_qfq', 'fwd_ret', 'label'] + feature_cols
out = df[keep_cols].dropna(subset=['label'])
out = out.dropna(subset=feature_cols, thresh=int(len(feature_cols) * 0.7))

out.to_pickle(OUT_PATH)
print(f'\n特征表已保存: {OUT_PATH}')
print(f'样本数: {len(out)}, 日期范围: {out["trade_date"].min().date()} ~ {out["trade_date"].max().date()}')
print(f'特征列({len(feature_cols)}个): {feature_cols}')
print(f'总耗时: {time.time()-t0:.0f}s')
