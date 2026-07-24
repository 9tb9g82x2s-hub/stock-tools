#!/usr/bin/env python3
"""
S004: OBV + MACD 擒牛 回测脚本
策略条件:
  1a: 近20天 OBV > MAOBV(20) 天数 >= 90%
  1b: 近5天 OBV 加速拉开 MAOBV（夹角>20度量化代理）
  2:  近30天 DIF>0 且 DEA>0 天数 >= 67%
  3:  MA5 上穿 MA10、MA20
  4:  前30天平台整理（可选加分项）
"""
import sqlite3, pandas as pd, numpy as np
import os, time
from datetime import datetime

# ========== 配置 ==========
DB_PATH = os.path.expanduser('~/stock-data/stock_all.db')
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

# 回测参数
TOP_STOCKS = 500          # 股票池（按流动性取Top N）
START_DATE = '2022-01-01' # 回测起始日
SIGNAL_GAP = 20           # 同股票信号间隔（交易日）
HOLD_PERIODS = [5, 10, 20, 30, 60]  # 持有期

# 策略参数（可调优）
OBV_MA_PERIOD = 20        # OBV均线周期
OBV_ABOVE_RATIO = 0.80    # 1a: OBV在均线上方的最小天数占比（基本运行在MA上方）
OBV_ACCEL_DAYS = 5        # 1b: 加速判断窗口（天）
OBV_ANGLE_THRESHOLD = 0.35  # 1b: 夹角阈值（归一化斜率，>0.35≈20°）
MACD_DAYS = 30            # 2: MACD考察窗口
MACD_ABOVE_RATIO = 0.67   # 2: DIF/DEA在零轴上方的最小天数占比（大多数时间>0）
MA_SHORT = 5              # 3: 短均线
MA_MID = 10               # 3: 中均线
MA_LONG = 20              # 3: 长均线
PLATFORM_DAYS = 30        # 4: 平台整理窗口
PLATFORM_MAX_AMP = 0.20   # 4: 最大振幅
PLATFORM_OFFSET = 10      # 4: 平台结束到信号日的偏移（天）


def get_stock_pool(n=500):
    """获取Top N流动性股票池，排除ST和亏损股"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 获取非ST股票
    cur.execute("SELECT DISTINCT ts_code FROM stock_list WHERE name NOT LIKE '%ST%' AND name NOT LIKE '%*ST%' AND name NOT LIKE '%退%'")
    clean_stocks = set(r[0] for r in cur.fetchall())

    # 按日均成交额排序取Top N（用2024年数据）
    cur.execute("""
        SELECT ts_code FROM daily
        WHERE trade_date >= '20240101' AND trade_date < '20260101'
        GROUP BY ts_code
        ORDER BY AVG(CAST(vol AS REAL) * CAST(close AS REAL)) DESC
        LIMIT ?
    """, (n * 2,))  # 多取一些，后面还要过滤
    ranked = [r[0] for r in cur.fetchall()]

    pool = [s for s in ranked if s in clean_stocks][:n]
    conn.close()
    return pool


def load_index_data():
    """加载沪深300基准数据"""
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql("""
            SELECT trade_date, CAST(close AS REAL) as close
            FROM daily WHERE ts_code='000300.SH'
            AND trade_date >= '20220101'
            ORDER BY trade_date
        """, conn)
        df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
        df = df.set_index('trade_date').sort_index()
    except:
        df = pd.DataFrame()
    conn.close()
    return df


def compute_obv(close, vol):
    """计算OBV（能量潮）"""
    diff = close.diff()
    sign = np.where(diff > 0, 1, np.where(diff < 0, -1, 0))
    sign[0] = 0
    return (sign * vol).cumsum()


def compute_macd(close, fast=12, slow=26, signal=9):
    """计算MACD指标"""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    bar = 2 * (dif - dea)
    return dif, dea, bar


def detect_signals(df):
    """
    对单只股票数据检测信号
    返回: [(信号日期, 信号强度), ...]
    """
    if len(df) < 260:  # 至少需要一年数据
        return []

    c = df['close'].values
    h = df['high'].values
    l = df['low'].values
    v = df['vol'].values
    dates = list(df.index)

    # ---- 计算指标 ----
    obv = compute_obv(pd.Series(c), pd.Series(v))
    maobv = pd.Series(obv).rolling(OBV_MA_PERIOD).mean().values
    dif, dea, bar = compute_macd(pd.Series(c))
    dif = dif.values
    dea = dea.values
    ma5 = pd.Series(c).rolling(MA_SHORT).mean().values
    ma10 = pd.Series(c).rolling(MA_MID).mean().values
    ma20 = pd.Series(c).rolling(MA_LONG).mean().values

    # ---- 逐日检测 ----
    signals = []
    for i in range(259, len(dates)):
        # 需要足够的历史数据
        if i < OBV_MA_PERIOD + OBV_ACCEL_DAYS:
            continue

        # 条件1a: 近20天 OBV > MAOBV 的天数占比
        obv_window = obv[i - 20:i+1]
        maobv_window = maobv[i - 20:i+1]
        valid = ~(np.isnan(obv_window) | np.isnan(maobv_window))
        if valid.sum() < 18:
            continue
        above_days = (obv_window[valid] > maobv_window[valid]).sum()
        cond_1a = above_days >= valid.sum() * OBV_ABOVE_RATIO

        # 条件1b: 近5天 OBV 加速拉开 MAOBV（夹角>20度）
        # 方法：计算(OBV-MAOBV)在近5天的线性回归斜率
        # 归一化：除以60天OBV标准差，消除量级差异
        # tan(20°)≈0.364，归一化斜率>0.35即夹角>20度
        obv_5 = obv[i - OBV_ACCEL_DAYS + 1:i+1]
        maobv_5 = maobv[i - OBV_ACCEL_DAYS + 1:i+1]
        obv_std_60 = np.nanstd(obv[max(0,i-59):i+1])
        if np.any(np.isnan(obv_5)) or np.any(np.isnan(maobv_5)) or obv_std_60 < 1:
            cond_1b = False
        else:
            # 近5天 (OBV-MAOBV) 差值的线性回归斜率，标准化
            diff_5 = obv_5 - maobv_5
            x = np.arange(len(diff_5))
            slope_diff, _ = np.polyfit(x, diff_5, 1)[:2]
            # 归一化斜率 = 日均差值增长 / OBV标准差 → 类比角度
            normalized_slope = slope_diff / obv_std_60
            cond_1b = normalized_slope > OBV_ANGLE_THRESHOLD

        # 条件2: 近30天 DIF>0 且 DEA>0 的天数
        dif_window = dif[i - MACD_DAYS:i+1]
        dea_window = dea[i - MACD_DAYS:i+1]
        valid_macd = ~(np.isnan(dif_window) | np.isnan(dea_window))
        if valid_macd.sum() < 20:
            continue
        above_zero = ((dif_window > 0) & (dea_window > 0) & valid_macd).sum()
        cond_2 = above_zero >= valid_macd.sum() * MACD_ABOVE_RATIO

        # 条件3: MA5 上穿 MA10、MA20
        if np.isnan(ma5[i]) or np.isnan(ma10[i]) or np.isnan(ma20[i]):
            continue
        if np.isnan(ma5[i-1]) or np.isnan(ma10[i-1]) or np.isnan(ma20[i-1]):
            continue
        # 今日: MA5 > MA10 and MA5 > MA20
        # 昨日: MA5 <= MA10 or MA5 <= MA20（发生了上穿）
        today_cross = (ma5[i] > ma10[i]) and (ma5[i] > ma20[i])
        yesterday_no_cross = (ma5[i-1] <= ma10[i-1]) or (ma5[i-1] <= ma20[i-1])
        cond_3 = today_cross and yesterday_no_cross

        # 条件4: 前30天平台整理（可选加分）
        plat_start = i - PLATFORM_DAYS - PLATFORM_OFFSET
        plat_end = i - PLATFORM_OFFSET
        cond_4 = False
        if plat_start >= 0:
            plat_h = np.max(h[plat_start:plat_end])
            plat_l = np.min(l[plat_start:plat_end])
            plat_mean = np.mean(c[plat_start:plat_end])
            if plat_mean > 0:
                amplitude = (plat_h - plat_l) / plat_mean
                cond_4 = amplitude < PLATFORM_MAX_AMP

        # 信号分级
        if cond_1a and cond_1b and cond_2 and cond_3:
            strength = 'strong' if cond_4 else 'solid'
        elif cond_1a and cond_2 and cond_3:
            strength = 'medium'
        elif cond_2 and cond_3:
            strength = 'weak'
        else:
            continue

        signals.append((dates[i], strength, cond_4))

    return signals


def deduplicate_signals(signals_by_stock):
    """同股票信号去重：20个交易日内只保留第一个"""
    all_signals = []
    for ts_code, sig_list in signals_by_stock:
        sig_list.sort(key=lambda x: x[0])  # 按日期排序
        last_date = None
        for sig in sig_list:
            if last_date is None or (sig[0] - last_date).days >= SIGNAL_GAP:
                all_signals.append((ts_code, *sig))
                last_date = sig[0]
    return all_signals


def run_backtest():
    print(f"{'='*60}")
    print(f"S004: OBV + MACD 擒牛 回测")
    print(f"{'='*60}")

    # Step 1: 股票池
    print("\n[1/5] 获取股票池...")
    stocks = get_stock_pool(TOP_STOCKS)
    print(f"  股票池: {len(stocks)} 只 (Top {TOP_STOCKS} 流动性)")

    # Step 2: 加载基准
    print("\n[2/5] 加载基准数据...")
    benchmark = load_index_data()
    print(f"  沪深300: {len(benchmark)} 个交易日" if len(benchmark) > 0 else "  无基准数据")

    # Step 3: 逐只计算信号
    print("\n[3/5] 逐只扫描信号...")
    conn = sqlite3.connect(DB_PATH)
    signals_by_stock = []
    total = len(stocks)
    t0 = time.time()

    for idx, ts_code in enumerate(stocks):
        if (idx + 1) % 50 == 0 or idx == 0:
            elapsed = time.time() - t0
            eta = elapsed / (idx + 1) * (total - idx - 1) if idx > 0 else 0
            print(f"  [{idx+1}/{total}] {ts_code} 已耗时 {elapsed:.0f}s 预计剩余 {eta:.0f}s")

        # 读取日线数据
        df = pd.read_sql(f"""
            SELECT trade_date,
                   CAST(open AS REAL) as open,
                   CAST(high AS REAL) as high,
                   CAST(low AS REAL) as low,
                   CAST(close AS REAL) as close,
                   CAST(vol AS REAL) as vol
            FROM daily
            WHERE ts_code = '{ts_code}'
              AND trade_date >= '20210101'
            ORDER BY trade_date
        """, conn)

        if len(df) < 260:
            continue

        df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')
        df = df.set_index('trade_date').sort_index()

        # 过滤：只处理2022年及之后的信号
        sigs = detect_signals(df)
        sigs = [(d, s, p) for d, s, p in sigs if d >= pd.Timestamp(START_DATE)]
        if sigs:
            signals_by_stock.append((ts_code, sigs))

    conn.close()
    print(f"  完成！共 {sum(len(s[1]) for s in signals_by_stock)} 个原始信号")

    # Step 4: 去重
    print("\n[4/5] 信号去重...")
    all_signals = deduplicate_signals(signals_by_stock)
    print(f"  去重后: {len(all_signals)} 个信号")

    # 按强度统计
    from collections import Counter
    strength_count = Counter(s[2] for s in all_signals)
    plat_count = sum(1 for s in all_signals if s[3])
    print(f"  strong: {strength_count.get('strong', 0)}  solid: {strength_count.get('solid', 0)}")
    print(f"  medium: {strength_count.get('medium', 0)}  weak: {strength_count.get('weak', 0)}")
    print(f"  含平台整理: {plat_count}")

    # Step 5: 计算持有收益
    print("\n[5/5] 计算持有期收益...")
    conn = sqlite3.connect(DB_PATH)

    # 预加载所有相关股票的close pivot
    all_codes = list(set(s[0] for s in all_signals))
    cs_str = ','.join(f"'{c}'" for c in all_codes)
    daily_all = pd.read_sql(f"""
        SELECT ts_code, trade_date, CAST(close AS REAL) as close
        FROM daily
        WHERE ts_code IN ({cs_str})
          AND trade_date >= '20220101'
        ORDER BY ts_code, trade_date
    """, conn)
    conn.close()

    daily_all['trade_date'] = pd.to_datetime(daily_all['trade_date'], format='%Y%m%d')
    close_pv = daily_all.pivot(index='trade_date', columns='ts_code', values='close').sort_index()
    all_dates = list(close_pv.index)

    results = []
    for ts_code, sig_date, strength, has_plat in all_signals:
        if ts_code not in close_pv.columns:
            continue
        cls_series = close_pv[ts_code].dropna()
        if sig_date not in cls_series.index:
            continue

        dates_list = list(cls_series.index)
        try:
            idx = dates_list.index(sig_date)
        except ValueError:
            continue

        entry_price = float(cls_series.iloc[idx])

        for hold_days in HOLD_PERIODS:
            fut_idx = idx + hold_days
            if fut_idx >= len(dates_list):
                continue
            exit_price = float(cls_series.iloc[fut_idx])
            ret = (exit_price / entry_price - 1) * 100

            # 同期基准收益
            bench_ret = None
            if len(benchmark) > 0 and sig_date in benchmark.index:
                b_dates = list(benchmark.index)
                try:
                    b_idx = b_dates.index(sig_date)
                    b_fut = b_idx + hold_days
                    if b_fut < len(b_dates):
                        b_entry = float(benchmark.iloc[b_idx].close)
                        b_exit = float(benchmark.iloc[b_fut].close)
                        bench_ret = (b_exit / b_entry - 1) * 100
                except (ValueError, IndexError):
                    pass

            results.append({
                'ts_code': ts_code,
                'date': sig_date.strftime('%Y-%m-%d'),
                'strength': strength,
                'has_platform': has_plat,
                'hold_days': hold_days,
                'return': ret,
                'benchmark_return': bench_ret,
                'excess_return': ret - bench_ret if bench_ret is not None else None,
                'entry_price': entry_price,
                'exit_price': exit_price,
            })

    df_results = pd.DataFrame(results)
    if len(df_results) == 0:
        print("  无有效信号！")
        return df_results

    # ========== 统计输出 ==========
    print(f"\n{'='*60}")
    print(f"回测结果汇总")
    print(f"{'='*60}")

    for strength_filter, label in [('all', '全部信号'), ('strong', '强信号(含平台)'),
                                     ('solid', '实信号'), ('medium', '中信号')]:
        if strength_filter == 'all':
            subset = df_results
        else:
            subset = df_results[df_results['strength'] == strength_filter]
            if len(subset) == 0:
                continue

        print(f"\n--- {label} ---")
        print(f"{'持有期':>6s}  {'信号数':>6s}  {'胜率':>8s}  {'均收益':>10s}  {'中位收益':>10s}  "
              f"{'超额收益':>10s}  {'最大单笔':>10s}  {'最小单笔':>10s}")
        print('-' * 85)

        for hold in HOLD_PERIODS:
            sub = subset[subset['hold_days'] == hold]
            if len(sub) < 5:
                continue
            win = (sub['return'] > 0).mean()
            avg = sub['return'].mean()
            med = sub['return'].median()
            excess = sub['excess_return'].mean() if 'excess_return' in sub.columns else 0
            mx = sub['return'].max()
            mn = sub['return'].min()
            print(f'{hold:>5d}天  {len(sub):>5d}笔  {win:>7.1%}  {avg:>+9.2f}%  {med:>+9.2f}%  '
                  f'{excess:>+9.2f}%  {mx:>+9.2f}%  {mn:>+9.2f}%')

    # 保存结果
    csv_path = os.path.join(OUT_DIR, 'results.csv')
    df_results.to_csv(csv_path, index=False)
    print(f"\n结果已保存: {csv_path}")
    print(f"总记录数: {len(df_results)}")

    return df_results


if __name__ == '__main__':
    run_backtest()
