#!/usr/bin/env python3
"""
S022 超跌突破确认策略 完整回测
================================
信号：T0 = 涨幅>7% 或 放量突破20日高点
      + T0前收盘在20日均线下方5%以上（超跌位置）
买入：T0次日开盘（T0+1 open）
出场：
  - 持有HOLD天后收盘卖出
  - 或：收盘跌破买入价×(1+SL) 止损出局
仓位：N_POS等权分仓（同时最多N_POS只），资金不够时跳过
股票池：剔除北交所 + ST + 亏损股

输出：
  - 净值曲线（逐日）→ s022_equity.csv
  - 交易明细      → s022_trades.csv
  - 年度汇总      → 打印

运行（Studio）：
  cd /Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-26-S021-三层RPS事件确认
  python3 s022_backtest.py

断点续跑：无需断点（全内存跑，10-15分钟完成）
"""
import sqlite3, pandas as pd, numpy as np, time, os
import warnings; warnings.filterwarnings('ignore')
from collections import defaultdict

# ========== 参数 ==========
DB    = os.path.expanduser('~/stock-data/stock_all.db')
OUT   = os.path.dirname(os.path.abspath(__file__))

LOOKBACK   = 25      # 信号回看天数
MA_WIN     = 20      # 均线窗口
VOL_WIN    = 20      # 成交量均线窗口

# 信号条件
PCT_THR    = 7.0     # 涨幅触发阈值（%）
VOL_MULT   = 1.5     # 放量突破倍数
# 超跌位置区间（与路径1验证口径严格一致：T0当天收盘/20日均线-1 在[-0.20,-0.05]）
# 用区间而非仅上限：排除深度超跌暴雷股(<-0.20)，虽然深超跌事件级更高但样本少且风险大
PRICE_MA_LO = -0.20  # 下限
PRICE_MA_HI = -0.05  # 上限

# 出场
HOLD       = 20      # 持仓天数
SL         = -0.12   # 止损线
TP         = None    # 止盈线（None=不设置）

# 组合
N_POS      = 5       # 最大同时持仓只数（等权分仓）
INIT_CASH  = 1_000_000.0  # 初始资金100万
START      = '20160101'
END        = '20260724'

t0 = time.time()
print("="*68)
print("S022 超跌突破确认策略 完整回测")
print("="*68, flush=True)

# ========== 1. 加载数据 ==========
print("加载数据...", flush=True)
conn = sqlite3.connect(DB)

# 股票池
codes_all = pd.read_sql("SELECT DISTINCT ts_code FROM daily", conn)['ts_code'].tolist()
codes_all = [c for c in codes_all if not c.endswith('.BJ')]
bl = (set(pd.read_sql("SELECT ts_code FROM blacklist_st", conn)['ts_code']) |
      set(pd.read_sql("SELECT ts_code FROM blacklist_loss", conn)['ts_code']))
universe = [c for c in codes_all if c not in bl]
univ_set = set(universe)

# 日线
daily = pd.read_sql(
    f"SELECT ts_code,trade_date,open,high,low,close,vol,pct_chg "
    f"FROM daily WHERE trade_date>='{START}' AND trade_date<='{END}'", conn)
conn.close()
for c in ['open','high','low','close','vol','pct_chg']:
    daily[c] = pd.to_numeric(daily[c], errors='coerce')
daily = daily[daily['ts_code'].isin(univ_set)].dropna(subset=['close','open']).query('close>0')
daily = daily.sort_values(['ts_code','trade_date']).reset_index(drop=True)

# 交易日序列
trade_dates = sorted(daily['trade_date'].unique().tolist())
date2idx    = {d: i for i, d in enumerate(trade_dates)}

# 按股票分组（dict of DataFrame）
dg = {c: g.reset_index(drop=True) for c, g in daily.groupby('ts_code')}
# 股票内日期→位置索引
stock_date2pos = {}
for code, g in dg.items():
    stock_date2pos[code] = {d: i for i, d in enumerate(g['trade_date'].values)}

print(f"  股票池: {len(universe)}只  交易日: {len(trade_dates)}天  "
      f"日线行数: {len(daily)}", flush=True)

# ========== 2. 预计算信号 ==========
print("预计算信号...", flush=True)
signals = defaultdict(list)   # date → [(code, buy_price_estimated)]

for code, g in dg.items():
    close = g['close'].values
    high  = g['high'].values
    vol   = g['vol'].values
    pct   = g['pct_chg'].values
    dates = g['trade_date'].values
    n     = len(g)

    for T in range(LOOKBACK, n - 2):
        td = dates[T]
        if td < START or td > END: continue

        # 均线 & 成交量均线（T0前不含T0）
        ma20      = close[T-MA_WIN:T].mean()
        vol_ma20  = vol[T-VOL_WIN:T].mean()
        h20       = high[T-LOOKBACK:T].max()

        # 信号条件
        cond_pct   = pct[T] > PCT_THR
        cond_break = (high[T] > h20) and (vol_ma20 > 0) and (vol[T] > VOL_MULT * vol_ma20)

        if not (cond_pct or cond_break): continue

        # 超跌位置：T0当天收盘 在20日均线下方5%-20%区间（与路径1口径一致）
        c_t0    = close[T]   # T0当天收盘
        if ma20 <= 0: continue
        ratio   = c_t0 / ma20 - 1
        if not (PRICE_MA_LO <= ratio < PRICE_MA_HI): continue  # 区间过滤

        # 买入价 = T0+1 开盘（在回测主循环里取）
        buy_date_idx = T + 1
        if buy_date_idx >= n: continue
        signals[dates[buy_date_idx]].append({
            'code': code,
            'T_in_stock': buy_date_idx,
            'stock_T0_idx': T,
        })

total_signals = sum(len(v) for v in signals.values())
print(f"  信号总数: {total_signals}  覆盖日期: {len(signals)}天", flush=True)

# ========== 3. 主回测循环 ==========
print("主回测循环...", flush=True)

cash      = INIT_CASH
positions = {}   # code → {'shares', 'buy_price', 'buy_date', 'T_idx', 'hold_left'}
equity_list = []
trade_list  = []

for dt in trade_dates:
    # -- 3a. 检查已有持仓：到期或止损 --
    to_close = []
    for code, pos in positions.items():
        g  = dg[code]
        Ti = pos['T_idx']
        if Ti >= len(g): to_close.append((code, 'expired', 0)); continue
        close_price = g['close'].iloc[Ti]
        ret         = close_price / pos['buy_price'] - 1
        hold_left   = pos['hold_left'] - 1

        if hold_left <= 0 or ret <= SL or (TP and ret >= TP):
            reason = 'tp' if (TP and ret >= TP) else ('sl' if ret <= SL else 'hold')
            to_close.append((code, reason, close_price))
        else:
            pos['hold_left'] = hold_left
            pos['T_idx']    = Ti + 1

    for code, reason, price in to_close:
        pos   = positions.pop(code)
        value = price * pos['shares'] if price > 0 else pos['buy_price'] * pos['shares'] * (1 + SL)
        cash += value
        trade_list.append({
            'code': code, 'buy_date': pos['buy_date'], 'sell_date': dt,
            'buy_price': pos['buy_price'], 'sell_price': price if price > 0 else pos['buy_price']*(1+SL),
            'shares': pos['shares'], 'reason': reason,
            'ret': price / pos['buy_price'] - 1 if price > 0 else SL,
        })

    # -- 3b. 新建仓 --
    if dt in signals:
        slots = N_POS - len(positions)
        if slots > 0:
            cands = signals[dt]
            np.random.shuffle(cands)          # 同日多信号随机排序
            for sig in cands[:slots]:
                code  = sig['code']
                if code in positions: continue
                g     = dg[code]
                Ti    = sig['T_in_stock']
                if Ti >= len(g): continue
                buy_p = g['open'].iloc[Ti]
                if buy_p <= 0 or pd.isna(buy_p): continue
                alloc = cash / (N_POS - len(positions))
                alloc = min(alloc, cash * 0.25)   # 单只最大25%
                if alloc < 1000: continue
                shares = int(alloc / buy_p / 100) * 100
                if shares <= 0: continue
                cost   = shares * buy_p
                if cost > cash: continue
                cash  -= cost
                positions[code] = {
                    'shares': shares, 'buy_price': buy_p,
                    'buy_date': dt, 'T_idx': Ti + 1,
                    'hold_left': HOLD,
                }

    # -- 3c. 记录净值 --
    pos_value = 0
    for code, pos in positions.items():
        g  = dg[code]
        Ti = pos['T_idx'] - 1   # 当前持仓已用T_idx+1，回退一格取当日收盘
        if Ti < 0 or Ti >= len(g): Ti = max(0, min(pos['T_idx'], len(g)-1))
        pos_value += g['close'].iloc[Ti] * pos['shares']
    equity_list.append({'date': dt, 'equity': cash + pos_value,
                        'cash': cash, 'pos_value': pos_value,
                        'n_pos': len(positions)})

# ========== 4. 输出 ==========
eq = pd.DataFrame(equity_list).set_index('date')
tr = pd.DataFrame(trade_list)

eq_csv = os.path.join(OUT, 's022_equity.csv')
tr_csv = os.path.join(OUT, 's022_trades.csv')
eq.to_csv(eq_csv)
if len(tr): tr.to_csv(tr_csv, index=False)
print(f"  净值曲线 → {eq_csv}")
print(f"  交易明细 → {tr_csv}", flush=True)

# ========== 5. 统计 ==========
print("\n" + "="*68)
init_eq = eq['equity'].iloc[0]
final_eq = eq['equity'].iloc[-1]
total_ret = final_eq / INIT_CASH - 1
years = len(trade_dates) / 250
cagr  = (final_eq / INIT_CASH) ** (1/years) - 1

dd = (eq['equity'] / eq['equity'].cummax() - 1)
mdd = dd.min()

# 夏普
daily_ret = eq['equity'].pct_change().dropna()
sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(250) if daily_ret.std() > 0 else 0

print(f"{'='*68}")
print(f"期间: {trade_dates[0]} ~ {trade_dates[-1]}  ({years:.1f}年)")
print(f"初始资金: ¥{INIT_CASH:,.0f}   最终: ¥{final_eq:,.0f}")
print(f"总收益: {total_ret*100:.1f}%   年化(CAGR): {cagr*100:.1f}%")
print(f"最大回撤: {mdd*100:.1f}%   夏普: {sharpe:.2f}")
print()

if len(tr):
    print(f"交易次数: {len(tr)}  胜率: {(tr['ret']>0).mean()*100:.1f}%")
    print(f"平均收益: {tr['ret'].mean()*100:.1f}%  中位: {tr['ret'].median()*100:.1f}%")
    print(f"止损出局: {(tr['reason']=='sl').sum()}次  到期出局: {(tr['reason']=='hold').sum()}次")
    print()

# 分年
print(f"{'年份':<6}{'年初净值':>12}{'年末净值':>12}{'年收益':>8}{'最大DD':>8}")
for yr in range(int(START[:4]), int(END[:4])+1):
    yr_data = eq[eq.index.str.startswith(str(yr))]
    if len(yr_data) < 5: continue
    y0 = yr_data['equity'].iloc[0]; y1 = yr_data['equity'].iloc[-1]
    yr_ret = y1/y0 - 1
    yr_dd  = (yr_data['equity'] / yr_data['equity'].cummax() - 1).min()
    print(f"{yr:<6}{y0:>12,.0f}{y1:>12,.0f}{yr_ret*100:>7.1f}%{yr_dd*100:>7.1f}%")

print(f"\n{'='*68}")
print(f"完成, 耗时 {(time.time()-t0)/60:.1f} 分钟")
print(f"{'='*68}", flush=True)
