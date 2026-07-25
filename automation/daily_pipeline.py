#!/usr/bin/env python3
"""
A股每日自动化流水线
====================
5步流程:
  1. 更新股票元数据（akshare）
  2. 增量下载最新K线数据（akshare, 5000+只）
  3. 批量计算技术指标（MA/OBV/MACD/RSI/布林带/KDJ/ATR）
  4. Ollama AI三层分析（可选 --skip-ollama 跳过）
  5. 生成每日分析HTML报告

用法:
  python automation/daily_pipeline.py                    # 完整流程
  python automation/daily_pipeline.py --skip-ollama      # 跳过AI分析
  python automation/daily_pipeline.py --step 1           # 只执行第1步
  python automation/daily_pipeline.py --days 10          # 只更新最近10天
"""

import sqlite3
import os
import sys
import time
import json
import argparse
import traceback
from datetime import datetime, timedelta
from pathlib import Path

# ── 配置 ──────────────────────────────────────────────
DB_PATH = os.path.expanduser("~/stock-data/stock_all.db")
REPORT_DIR = os.path.expanduser("~/stock-data/reports")
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))  # automation/
OLLAMA_URL = "http://localhost:11434/api/generate"

# 技术指标参数
INDICATOR_PARAMS = {
    "ma_periods": [5, 10, 20, 60, 120, 250],
    "rsi_periods": [6, 14],
    "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
    "boll_period": 20, "boll_std": 2,
    "kdj_n": 9,
    "atr_period": 14,
}

# 信号阈值
SIGNAL_THRESHOLDS = {
    "rsi_oversold": 30,
    "rsi_overbought": 70,
    "kdj_oversold": 20,
    "kdj_overbought": 80,
    "vol_expand_ratio": 1.5,
    "ma_convergence_pct": 3.0,   # 均线粘合阈值
}

# ── 日志 ──────────────────────────────────────────────
STEP_EMOJI = ["📋", "📥", "📊", "🤖", "📄"]
PIPE = "│"


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    tags = {"INFO": "⚪", "OK": "✅", "WARN": "⚠️", "ERR": "❌", "STEP": "🔷"}
    tag = tags.get(level, "⚪")
    print(f"{tag} [{ts}] {msg}", flush=True)


def log_step(step_num, total, msg):
    print(f"\n{'='*60}")
    print(f"  {STEP_EMOJI[step_num-1]} Step {step_num}/{total}: {msg}")
    print(f"{'='*60}", flush=True)


# ═══════════════════════════════════════════════════════
# Step 1: 更新股票元数据
# ═══════════════════════════════════════════════════════
def update_stock_metadata():
    """从 akshare 获取最新股票列表，更新 stock_list 表"""
    log_step(1, 5, "更新股票元数据")

    try:
        import akshare as ak
    except ImportError:
        log("akshare 未安装，尝试安装...", "WARN")
        os.system(f"{sys.executable} -m pip install akshare -q")
        import akshare as ak

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 确保 stock_list 表存在（与现有 schema 兼容）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS stock_list (
            ts_code TEXT PRIMARY KEY,
            symbol TEXT,
            name TEXT,
            area TEXT,
            industry TEXT,
            cnspell TEXT,
            market TEXT,
            list_date TEXT,
            act_name TEXT,
            act_ent_type TEXT
        )
    """)

    # 获取当前 stock_list 数量
    cur.execute("SELECT COUNT(*) FROM stock_list")
    existing = cur.fetchone()[0]
    log(f"现有股票元数据: {existing} 只")

    # 获取全量A股列表
    log("正在从 akshare 获取最新股票列表...")
    try:
        df = ak.stock_info_a_code_name()
        log(f"akshare 返回 {len(df)} 只股票")
    except Exception as e:
        log(f"获取股票列表失败: {e}", "ERR")
        # 尝试备用接口
        try:
            df = ak.stock_zh_a_spot_em()
            df = df[['代码', '名称']].rename(columns={'代码': 'code', '名称': 'name'})
            log(f"备用接口返回 {len(df)} 只股票")
        except Exception as e2:
            log(f"备用接口也失败: {e2}", "ERR")
            conn.close()
            return existing

    # 解析股票列表
    new_count = 0
    updated_count = 0

    for _, row in df.iterrows():
        code = str(row.get('code', row.get('代码', ''))).strip()
        name = str(row.get('name', row.get('名称', ''))).strip()

        if not code or len(code) != 6:
            continue

        # 判断市场
        if code.startswith('6'):
            ts_code = f"{code}.SH"
            market = "SH"
        elif code.startswith(('0', '3')):
            ts_code = f"{code}.SZ"
            market = "SZ"
        elif code.startswith(('4', '8')):
            ts_code = f"{code}.BJ"
            market = "BJ"
        else:
            continue

        # 检查是否存在
        cur.execute("SELECT name FROM stock_list WHERE ts_code=?", (ts_code,))
        r = cur.fetchone()
        if r:
            # 更新名称（可能改名）
            if r[0] != name:
                cur.execute("UPDATE stock_list SET name=? WHERE ts_code=?",
                            (name, ts_code))
                updated_count += 1
        else:
            # INSERT OR IGNORE 方式，兼容不同 schema
            try:
                cur.execute("""
                    INSERT INTO stock_list (ts_code, symbol, name, market)
                    VALUES (?, ?, ?, ?)
                """, (ts_code, code, name, market))
            except Exception:
                # 如果字段更多，尝试完整字段
                cur.execute("""
                    INSERT OR IGNORE INTO stock_list (ts_code, symbol, name, market)
                    VALUES (?, ?, ?, ?)
                """, (ts_code, code, name, market))
            new_count += 1

        if (new_count + updated_count) % 500 == 0:
            conn.commit()

    conn.commit()

    cur.execute("SELECT COUNT(*) FROM stock_list")
    total = cur.fetchone()[0]

    log(f"元数据更新完成: 新增 {new_count} 只, 更新 {updated_count} 只, 总计 {total} 只", "OK")
    conn.close()
    return total


# ═══════════════════════════════════════════════════════
# Step 2: 增量下载最新K线数据
# ═══════════════════════════════════════════════════════
def update_daily_klines(days=30):
    """
    增量下载日K线数据，补全最近N天
    策略：
      - 优先用 stock_zh_a_spot_em() 批量获取今日快照（1次API调用，5000+只）
      - 其他缺失日期用 stock_zh_a_daily()（新浪API，更稳定）逐股补
    """
    log_step(2, 5, f"增量下载K线数据（最近 {days} 天）")

    try:
        import akshare as ak
        import pandas as pd
    except ImportError:
        log("依赖缺失", "ERR")
        return 0

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 确保 daily 表存在
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily (
            ts_code TEXT,
            trade_date TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            pre_close REAL,
            change REAL,
            pct_chg REAL,
            vol REAL,
            amount REAL,
            PRIMARY KEY (ts_code, trade_date)
        )
    """)
    conn.commit()

    # 获取数据库最新日期
    cur.execute("SELECT MAX(trade_date) FROM daily")
    latest = cur.fetchone()[0]
    today = datetime.now()
    today_str = today.strftime('%Y%m%d')

    if latest:
        log(f"数据库最新日期: {latest}")
        start_dt = datetime.strptime(latest, '%Y%m%d') + timedelta(days=1)
    else:
        start_dt = today - timedelta(days=365 * 3)
        log(f"数据库为空，从 {start_dt.strftime('%Y%m%d')} 开始下载")

    if start_dt > today:
        log("数据已是最新，无需更新", "OK")
        conn.close()
        return 0

    start_str = start_dt.strftime('%Y%m%d')
    log(f"补数据范围: {start_str} → {today_str}")

    new_count = 0
    fail_count = 0

    # ─── 策略A: stock_zh_a_spot_em() 批量获取今日快照 ───
    if today_str >= start_str:
        log(f"尝试批量获取今日快照 ({today_str})...")
        try:
            df_spot = ak.stock_zh_a_spot_em()
            if df_spot is not None and len(df_spot) > 0:
                log(f"获取到 {len(df_spot)} 只股票今日数据", "OK")

                for _, row in df_spot.iterrows():
                    try:
                        code = str(row['代码']).strip()
                        if len(code) != 6:
                            continue

                        # 判断市场后缀
                        if code.startswith('6'):
                            ts_code = f"{code}.SH"
                        elif code.startswith(('0', '3')):
                            ts_code = f"{code}.SZ"
                        elif code.startswith(('4', '8')):
                            ts_code = f"{code}.BJ"
                        else:
                            continue

                        open_v = float(row.get('今开', 0) or 0)
                        high_v = float(row.get('最高', 0) or 0)
                        low_v = float(row.get('最低', 0) or 0)
                        close_v = float(row.get('最新价', 0) or 0)
                        vol_v = float(row.get('成交量', 0) or 0)
                        amount_v = float(row.get('成交额', 0) or 0)
                        pct_v = float(row.get('涨跌幅', 0) or 0)

                        if close_v == 0:
                            continue

                        cur.execute("""
                            INSERT OR REPLACE INTO daily
                            (ts_code, trade_date, open, high, low, close, vol, amount, pct_chg)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (ts_code, today_str, open_v, high_v, low_v,
                              close_v, vol_v, amount_v, pct_v))
                        new_count += 1

                    except (ValueError, TypeError, KeyError):
                        continue

                conn.commit()
                log(f"今日快照写入: {new_count} 条", "OK")

                # 如果只需要今天的数据，直接返回
                if start_str == today_str:
                    # 统计
                    cur.execute("SELECT COUNT(DISTINCT ts_code) FROM daily")
                    total_stocks = cur.fetchone()[0]
                    cur.execute("SELECT MAX(trade_date) FROM daily")
                    final_latest = cur.fetchone()[0]
                    conn.close()
                    log(f"K线下载完成: {total_stocks} 只有数据, 新增 {new_count} 条", "OK")
                    log(f"数据最新日期: {final_latest}", "OK")
                    return new_count

                # 否则 start_str 可能早于 today_str，还需补之前的数据
                # 更新 start_dt 到 today_str 前一天
                start_dt_for_hist = start_dt
                # 如果 start_str < today_str, 还需要补历史
                end_dt_for_hist = today - timedelta(days=1)

            else:
                log("spot 接口返回空数据", "WARN")
                start_dt_for_hist = start_dt
                end_dt_for_hist = today

        except Exception as e:
            log(f"spot 接口失败: {e}，回退到逐股下载", "WARN")
            start_dt_for_hist = start_dt
            end_dt_for_hist = today
    else:
        start_dt_for_hist = start_dt
        end_dt_for_hist = today

    # ─── 策略B: 逐股补历史缺失日期（仅当需要时）───
    # 先检查是否还需要补
    hist_start = start_dt_for_hist.strftime('%Y%m%d')
    cur.execute("SELECT MAX(trade_date) FROM daily")
    db_latest = cur.fetchone()[0]
    need_full_dl = (db_latest < today_str) and (db_latest and db_latest != today_str)

    if not need_full_dl and db_latest == today_str:
        log("所有日期已完成，无需补历史", "OK")
        conn.close()
        return new_count

    log(f"补历史K线: 逐股下载（新浪API）...")

    # 获取需要更新的股票
    cur.execute("SELECT ts_code FROM stock_list ORDER BY ts_code")
    codes = [r[0] for r in cur.fetchall()]

    if not codes:
        log("stock_list 为空", "WARN")
        conn.close()
        return new_count

    # 优先更新没有最新数据的股票
    cur.execute(f"""
        SELECT ts_code FROM stock_list 
        WHERE ts_code NOT IN (
            SELECT DISTINCT ts_code FROM daily WHERE trade_date >= '{hist_start}'
        )
        ORDER BY ts_code
    """)
    need_update = [r[0] for r in cur.fetchall()]

    if not need_update:
        log("所有股票已有最新数据", "OK")
        conn.close()
        return new_count

    log(f"需要逐股更新: {len(need_update)} 只（共 {len(codes)} 只）")

    batch_size = 50
    total_batches = (len(need_update) + batch_size - 1) // batch_size
    ak_start = hist_start
    ak_end = today_str

    # 限制最多下载 batch 数，避免无限运行
    MAX_BATCHES = 40  # 最多 2000 只股票
    if total_batches > MAX_BATCHES:
        log(f"股票数过多({len(need_update)}), 限制为 {MAX_BATCHES * batch_size} 只", "WARN")
        need_update = need_update[:MAX_BATCHES * batch_size]
        total_batches = MAX_BATCHES

    consecutive_fails = 0

    for batch_idx in range(0, len(need_update), batch_size):
        batch_codes = need_update[batch_idx:batch_idx + batch_size]
        batch_num = batch_idx // batch_size + 1

        for ts_code in batch_codes:
            if consecutive_fails >= 10:
                log(f"连续失败 {consecutive_fails} 次，暂停下载", "ERR")
                break

            try:
                parts = ts_code.split('.')
                pure_code = parts[0]
                market = parts[1] if len(parts) > 1 else ''

                # 使用新浪API（stock_zh_a_daily），比 eastmoney 稳定
                prefix = "sh" if pure_code.startswith("6") else (
                    "sz" if pure_code.startswith(("0", "3")) else "bj"
                )
                symbol = f"{prefix}{pure_code}"

                df = ak.stock_zh_a_daily(
                    symbol=symbol,
                    start_date=ak_start,
                    end_date=ak_end,
                    adjust="qfq"
                )

                if df is None or df.empty:
                    continue

                consecutive_fails = 0  # 成功后重置

                for _, row in df.iterrows():
                    date_val = str(row.get('date', ''))[:10]
                    trade_date = date_val.replace('-', '')
                    if len(trade_date) != 8 or trade_date == 'nan':
                        continue

                    cur.execute("""
                        INSERT OR REPLACE INTO daily
                        (ts_code, trade_date, open, high, low, close, vol, amount)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        ts_code, trade_date,
                        float(row.get('open', 0) or 0),
                        float(row.get('high', 0) or 0),
                        float(row.get('low', 0) or 0),
                        float(row.get('close', 0) or 0),
                        float(row.get('volume', 0) or 0),
                        float(row.get('amount', 0) or 0),
                    ))
                    new_count += 1

                time.sleep(0.3)  # 新浪API也需要节制

            except Exception as e:
                fail_count += 1
                consecutive_fails += 1
                time.sleep(1.0)

        conn.commit()

        pct = batch_num / total_batches * 100
        log(f"  进度: {batch_num}/{total_batches} 批 ({pct:.0f}%) — "
            f"新增 {new_count} 条, 失败 {fail_count} 条")

        if consecutive_fails >= 10:
            break  # 同时跳出外层循环

        time.sleep(1)

    if consecutive_fails >= 10:
        log("下载因连续失败提前终止", "WARN")

    # ─── 统计结果 ───
    cur.execute("SELECT COUNT(DISTINCT ts_code) FROM daily")
    total_stocks = cur.fetchone()[0]
    cur.execute("SELECT MAX(trade_date) FROM daily")
    final_latest = cur.fetchone()[0]

    conn.close()

    log(f"K线下载完成: {len(codes)} 只 → {total_stocks} 只有数据, "
        f"新增 {new_count} 条, 失败 {fail_count} 条", "OK")
    log(f"数据最新日期: {final_latest}", "OK")

    return new_count


# ═══════════════════════════════════════════════════════
# Step 3: 批量计算技术指标
# ═══════════════════════════════════════════════════════
class IndicatorCalculator:
    """批量技术指标计算器"""

    def __init__(self):
        self.params = INDICATOR_PARAMS

    def calc_ma(self, closes, period):
        """简单移动平均"""
        if len(closes) < period:
            return [None] * len(closes)
        result = [None] * (period - 1)
        for i in range(period - 1, len(closes)):
            window = closes[i - period + 1:i + 1]
            valid = [v for v in window if v is not None]
            result.append(sum(valid) / len(valid) if valid else None)
        return result

    def calc_ema(self, data, period):
        """指数移动平均"""
        if len(data) < period:
            return [None] * len(data)
        k = 2 / (period + 1)
        result = [None] * (period - 1)
        valid_start = [d for d in data[:period] if d is not None]
        if not valid_start:
            return [None] * len(data)
        ema_val = sum(valid_start) / len(valid_start)
        result.append(ema_val)
        for i in range(period, len(data)):
            if data[i] is not None:
                ema_val = data[i] * k + ema_val * (1 - k)
            result.append(ema_val)
        return result

    def calc_macd(self, closes):
        """MACD (12, 26, 9)"""
        fast, slow, sig = self.params["macd_fast"], self.params["macd_slow"], self.params["macd_signal"]
        ema_fast = self.calc_ema(closes, fast)
        ema_slow = self.calc_ema(closes, slow)

        dif = []
        for ef, es in zip(ema_fast, ema_slow):
            if ef is not None and es is not None:
                dif.append(ef - es)
            else:
                dif.append(None)

        dea = self.calc_ema(dif, sig)
        macd_bar = []
        for d, e in zip(dif, dea):
            if d is not None and e is not None:
                macd_bar.append(2 * (d - e))
            else:
                macd_bar.append(None)

        return dif, dea, macd_bar

    def calc_rsi(self, closes, period=14):
        """RSI"""
        if len(closes) < period + 1:
            return [None] * len(closes)

        deltas = []
        for i in range(1, len(closes)):
            if closes[i] is not None and closes[i-1] is not None:
                deltas.append(closes[i] - closes[i-1])
            else:
                deltas.append(0)

        rsi = [None]  # 第一天无RSI

        gains = [max(d, 0) for d in deltas]
        losses = [abs(min(d, 0)) for d in deltas]

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                rsi.append(100)
            else:
                rs = avg_gain / avg_loss
                rsi.append(round(100 - 100 / (1 + rs), 2))

        return rsi

    def calc_kdj(self, highs, lows, closes, n=9):
        """KDJ"""
        if len(closes) < n:
            return ([None]*len(closes), [None]*len(closes), [None]*len(closes))

        k_vals, d_vals, j_vals = [], [], []
        prev_k, prev_d = 50, 50

        for i in range(len(closes)):
            if i < n - 1:
                k_vals.append(None)
                d_vals.append(None)
                j_vals.append(None)
                continue

            hh = max(h for h in highs[i-n+1:i+1] if h is not None)
            ll = min(l for l in lows[i-n+1:i+1] if l is not None)

            if hh == ll:
                rsv = 50
            else:
                rsv = (closes[i] - ll) / (hh - ll) * 100

            k = prev_k * 2/3 + rsv / 3
            d = prev_d * 2/3 + k / 3
            j = 3 * k - 2 * d

            k_vals.append(round(k, 2))
            d_vals.append(round(d, 2))
            j_vals.append(round(j, 2))
            prev_k, prev_d = k, d

        return k_vals, d_vals, j_vals

    def calc_bollinger(self, closes, period=20, std_mult=2):
        """布林带"""
        ma = self.calc_ma(closes, period)
        upper, lower = [None]*len(closes), [None]*len(closes)

        for i in range(period-1, len(closes)):
            window = closes[i-period+1:i+1]
            valid = [v for v in window if v is not None]
            if len(valid) < 2:
                continue
            mean = sum(valid) / len(valid)
            variance = sum((v - mean)**2 for v in valid) / len(valid)
            std = variance ** 0.5
            upper[i] = round(mean + std_mult * std, 2)
            lower[i] = round(mean - std_mult * std, 2)

        return upper, lower

    def calc_atr(self, highs, lows, closes, period=14):
        """ATR 平均真实波幅"""
        if len(closes) < period + 1:
            return [None] * len(closes)

        tr = [None]  # 第一天无TR
        for i in range(1, len(closes)):
            h = highs[i] or 0
            l = lows[i] or 0
            pc = closes[i-1] or 0
            tr.append(max(h - l, abs(h - pc), abs(l - pc)))

        # EMA of TR
        valid_tr = [t for t in tr[1:period+1] if t is not None]
        if not valid_tr:
            return [None] * len(closes)
        atr_val = sum(valid_tr) / len(valid_tr)
        atr = [None] * (period) + [round(atr_val, 2)]

        k = 2 / (period + 1)
        for i in range(period + 1, len(tr)):
            if tr[i] is not None:
                atr_val = tr[i] * k + atr_val * (1 - k)
            atr.append(round(atr_val, 2))

        return atr

    def calc_obv(self, closes, volumes):
        """OBV 能量潮"""
        if len(closes) < 2:
            return [0] * len(closes)
        obv = [0]
        for i in range(1, len(closes)):
            if closes[i] is not None and closes[i-1] is not None:
                if closes[i] > closes[i-1]:
                    obv.append(obv[-1] + (volumes[i] or 0))
                elif closes[i] < closes[i-1]:
                    obv.append(obv[-1] - (volumes[i] or 0))
                else:
                    obv.append(obv[-1])
            else:
                obv.append(obv[-1])
        return obv

    def calc_all(self, highs, lows, closes, volumes):
        """一次性计算所有指标"""
        result = {}

        # MA
        for p in self.params["ma_periods"]:
            result[f"MA{p}"] = self.calc_ma(closes, p)

        # RSI
        result["RSI6"] = self.calc_rsi(closes, 6)
        result["RSI14"] = self.calc_rsi(closes, 14)

        # MACD
        dif, dea, bar = self.calc_macd(closes)
        result["MACD_DIF"] = dif
        result["MACD_DEA"] = dea
        result["MACD_BAR"] = bar

        # KDJ
        k, d, j = self.calc_kdj(highs, lows, closes)
        result["KDJ_K"] = k
        result["KDJ_D"] = d
        result["KDJ_J"] = j

        # Bollinger
        upper, lower = self.calc_bollinger(closes)
        result["BOLL_UPPER"] = upper
        result["BOLL_LOWER"] = lower

        # ATR
        result["ATR14"] = self.calc_atr(highs, lows, closes)

        # OBV
        result["OBV"] = self.calc_obv(closes, volumes)

        return result


def batch_calculate_indicators(max_stocks=None):
    """批量计算技术指标并写入数据库"""
    log_step(3, 5, "批量计算技术指标")

    try:
        import pandas as pd
        import numpy as np
    except ImportError:
        log("pandas/numpy 缺失", "ERR")
        return {"error": "missing deps"}

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # 创建指标结果表
    cur.execute("""
        CREATE TABLE IF NOT EXISTS indicators (
            ts_code TEXT,
            calc_date TEXT,
            -- MA
            ma5 REAL, ma10 REAL, ma20 REAL, ma60 REAL, ma120 REAL, ma250 REAL,
            -- RSI
            rsi6 REAL, rsi14 REAL,
            -- MACD
            macd_dif REAL, macd_dea REAL, macd_bar REAL,
            -- KDJ
            kdj_k REAL, kdj_d REAL, kdj_j REAL,
            -- Bollinger
            boll_upper REAL, boll_lower REAL,
            -- ATR, OBV
            atr14 REAL, obv REAL,
            -- 衍生信号
            ma_cross TEXT,
            rsi_signal TEXT,
            macd_signal TEXT,
            kdj_signal TEXT,
            vol_ratio_5 REAL,
            vol_ratio_20 REAL,
            PRIMARY KEY (ts_code, calc_date)
        )
    """)

    # 获取最新交易日
    cur.execute("SELECT MAX(trade_date) FROM daily")
    latest = cur.fetchone()[0]
    if not latest:
        log("daily 表为空", "ERR")
        conn.close()
        return {"error": "no data"}
    log(f"分析日期: {latest}")

    # 获取所有有数据的股票
    cur.execute("SELECT DISTINCT ts_code FROM daily")
    all_codes = [r[0] for r in cur.fetchall()]
    if max_stocks:
        all_codes = all_codes[:max_stocks]
    log(f"待计算股票: {len(all_codes)} 只")

    calculator = IndicatorCalculator()
    done = 0
    skipped = 0
    summary = {
        "date": latest,
        "total_stocks": len(all_codes),
        "ma_bullish": 0,
        "ma_bearish": 0,
        "rsi_oversold": 0,
        "rsi_overbought": 0,
        "macd_golden": 0,
        "macd_dead": 0,
        "kdj_oversold": 0,
        "kdj_overbought": 0,
        "vol_surge": 0,
        "signals": [],
    }

    batch_records = []
    t = INDICATOR_PARAMS
    th = SIGNAL_THRESHOLDS

    for ts_code in all_codes:
        try:
            df = pd.read_sql(f"""
                SELECT trade_date,
                       CAST(open AS REAL) o, CAST(high AS REAL) h,
                       CAST(low AS REAL) l, CAST(close AS REAL) c,
                       CAST(vol AS REAL) v, CAST(amount AS REAL) a,
                       CAST(pct_chg AS REAL) pct
                FROM daily WHERE ts_code='{ts_code}'
                AND trade_date >= '20240101'
                ORDER BY trade_date
            """, conn)

            if len(df) < 60:
                skipped += 1
                continue

            closes = df['c'].tolist()
            highs = df['h'].tolist()
            lows = df['l'].tolist()
            volumes = df['v'].tolist()

            indicators = calculator.calc_all(highs, lows, closes, volumes)

            # 取最新值
            idx = -1

            ma5_v = indicators["MA5"][idx]
            ma10_v = indicators["MA10"][idx]
            ma20_v = indicators["MA20"][idx]
            ma60_v = indicators["MA60"][idx]
            ma120_v = indicators["MA120"][idx]
            ma250_v = indicators["MA250"][idx]

            rsi6_v = indicators["RSI6"][idx]
            rsi14_v = indicators["RSI14"][idx]
            macd_dif_v = indicators["MACD_DIF"][idx]
            macd_dea_v = indicators["MACD_DEA"][idx]
            macd_bar_v = indicators["MACD_BAR"][idx]
            kdj_k_v = indicators["KDJ_K"][idx]
            kdj_d_v = indicators["KDJ_D"][idx]
            kdj_j_v = indicators["KDJ_J"][idx]
            boll_u = indicators["BOLL_UPPER"][idx]
            boll_l = indicators["BOLL_LOWER"][idx]
            atr_v = indicators["ATR14"][idx]
            obv_v = indicators["OBV"][idx]

            # ── 衍生信号判断 ──
            # 均线排列
            if ma5_v and ma10_v and ma20_v:
                if ma5_v > ma10_v > ma20_v:
                    ma_cross = "多头"
                    summary["ma_bullish"] += 1
                elif ma5_v < ma10_v < ma20_v:
                    ma_cross = "空头"
                    summary["ma_bearish"] += 1
                else:
                    ma_cross = "缠绕"
            else:
                ma_cross = None

            # RSI
            if rsi14_v:
                if rsi14_v < th["rsi_oversold"]:
                    rsi_sig = "超卖"
                    summary["rsi_oversold"] += 1
                elif rsi14_v > th["rsi_overbought"]:
                    rsi_sig = "超买"
                    summary["rsi_overbought"] += 1
                else:
                    rsi_sig = "中性"
            else:
                rsi_sig = None

            # MACD
            if macd_dif_v and macd_dea_v:
                if macd_dif_v > macd_dea_v:
                    macd_sig = "金叉"
                    summary["macd_golden"] += 1
                else:
                    macd_sig = "死叉"
                    summary["macd_dead"] += 1
            else:
                macd_sig = None

            # KDJ
            if kdj_k_v and kdj_d_v:
                if kdj_k_v < th["kdj_oversold"] and kdj_d_v < th["kdj_oversold"]:
                    kdj_sig = "超卖"
                    summary["kdj_oversold"] += 1
                elif kdj_k_v > th["kdj_overbought"] and kdj_d_v > th["kdj_overbought"]:
                    kdj_sig = "超买"
                    summary["kdj_overbought"] += 1
                elif kdj_k_v > kdj_d_v:
                    kdj_sig = "金叉"
                else:
                    kdj_sig = "死叉"
            else:
                kdj_sig = None

            # 成交量比值
            if len(volumes) >= 6:
                v5_avg = sum(v for v in volumes[-6:-1] if v) / max(1, sum(1 for v in volumes[-6:-1] if v))
                vol5 = volumes[-1] / v5_avg if v5_avg else 1
            else:
                vol5 = 1
            if len(volumes) >= 21:
                v20_avg = sum(v for v in volumes[-21:-1] if v) / max(1, sum(1 for v in volumes[-21:-1] if v))
                vol20 = volumes[-1] / v20_avg if v20_avg else 1
            else:
                vol20 = 1

            if vol5 > th["vol_expand_ratio"]:
                summary["vol_surge"] += 1

            # 综合信号 - 只收集有意义的信号
            close_v = closes[idx]
            pct_v = df['pct'].iloc[idx] if 'pct' in df.columns else None

            has_signal = (
                (ma_cross == "多头" and rsi14_v and rsi14_v < 60) or  # 多头初期
                (rsi_sig == "超卖") or
                (macd_sig == "金叉" and rsi14_v and rsi14_v < 50) or
                (kdj_sig == "超卖")
            )

            if has_signal:
                # 获取名称
                cur.execute("SELECT name, industry FROM stock_list WHERE ts_code=?", (ts_code,))
                nr = cur.fetchone()
                stock_name = nr[0] if nr else ts_code
                industry = nr[1] if nr and len(nr) > 1 else ""

                summary["signals"].append({
                    "ts_code": ts_code,
                    "name": stock_name,
                    "industry": industry,
                    "close": round(close_v, 2) if close_v else None,
                    "pct_chg": round(pct_v, 2) if pct_v else None,
                    "ma_cross": ma_cross,
                    "rsi14": rsi14_v,
                    "rsi_sig": rsi_sig,
                    "macd_sig": macd_sig,
                    "kdj_sig": kdj_sig,
                    "vol_ratio": round(vol5, 2),
                })

            # 写入数据库
            batch_records.append((
                ts_code, latest,
                ma5_v, ma10_v, ma20_v, ma60_v, ma120_v, ma250_v,
                rsi6_v, rsi14_v,
                macd_dif_v, macd_dea_v, macd_bar_v,
                kdj_k_v, kdj_d_v, kdj_j_v,
                boll_u, boll_l,
                atr_v, obv_v if obv_v else None,
                ma_cross, rsi_sig, macd_sig, kdj_sig,
                round(vol5, 2), round(vol20, 2),
            ))

            if len(batch_records) >= 500:
                cur.executemany("""
                    INSERT OR REPLACE INTO indicators
                    (ts_code, calc_date, ma5, ma10, ma20, ma60, ma120, ma250,
                     rsi6, rsi14, macd_dif, macd_dea, macd_bar,
                     kdj_k, kdj_d, kdj_j, boll_upper, boll_lower,
                     atr14, obv, ma_cross, rsi_signal, macd_signal, kdj_signal,
                     vol_ratio_5, vol_ratio_20)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, batch_records)
                conn.commit()
                batch_records = []

            done += 1
            if done % 1000 == 0:
                log(f"  已计算: {done}/{len(all_codes)}, 跳过: {skipped}")

        except Exception as e:
            skipped += 1
            if skipped < 5:
                log(f"  {ts_code} 计算失败: {str(e)[:80]}", "WARN")

    # 写入剩余
    if batch_records:
        cur.executemany("""
            INSERT OR REPLACE INTO indicators
            (ts_code, calc_date, ma5, ma10, ma20, ma60, ma120, ma250,
             rsi6, rsi14, macd_dif, macd_dea, macd_bar,
             kdj_k, kdj_d, kdj_j, boll_upper, boll_lower,
             atr14, obv, ma_cross, rsi_signal, macd_signal, kdj_signal,
             vol_ratio_5, vol_ratio_20)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, batch_records)
        conn.commit()

    conn.close()

    log(f"指标计算完成: 成功 {done} 只, 跳过 {skipped} 只", "OK")
    log(f"信号概览: 多头={summary['ma_bullish']}, 空头={summary['ma_bearish']}, "
        f"超卖={summary['rsi_oversold']}, 超买={summary['rsi_overbought']}, "
        f"MACD金叉={summary['macd_golden']}, 放量={summary['vol_surge']}", "OK")

    return summary


# ═══════════════════════════════════════════════════════
# Step 4: Ollama AI分析（三层）
# ═══════════════════════════════════════════════════════
def check_ollama():
    """检查 Ollama 是否可用"""
    import urllib.request
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            models = [m["name"] for m in data.get("models", [])]
            return True, models
    except Exception:
        return False, []


def ollama_generate(prompt, model, timeout=300):
    """调用 Ollama 生成"""
    import urllib.request
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 2048}
    }).encode("utf-8")

    req = urllib.request.Request(OLLAMA_URL, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read())
            return result.get("response", "")
    except Exception as e:
        log(f"Ollama({model}) 调用失败: {e}", "ERR")
        return None


def ollama_three_layer_analysis(summary, conn):
    """三层AI分析 - 仅在Ollama可用时执行"""
    log_step(4, 5, "Ollama AI 三层分析")

    ok, models = check_ollama()
    if not ok:
        log("Ollama 不可用，跳过 AI 分析", "WARN")
        return {"status": "skipped", "reason": "Ollama not available"}

    log(f"可用模型: {', '.join(models[:8])}...")

    # 检查所需模型
    model_l1 = "qwen3:8b-32k" if "qwen3:8b-32k" in models else None
    model_l2 = "deepseek-r1:70b" if "deepseek-r1:70b" in models else (
        "deepseek-r1:32b" if "deepseek-r1:32b" in models else None
    )
    model_l3 = model_l2 or model_l1  # 第三层复用

    if not model_l1:
        log("qwen3:8b-32k 不存在，降级使用可用模型", "WARN")
        model_l1 = models[0] if models else None

    if not model_l1:
        log("无可用模型", "ERR")
        return {"status": "skipped", "reason": "No models"}

    log(f"三层模型: L1={model_l1}, L2={model_l2}, L3={model_l3}")

    # ── Layer 1: 初筛（qwen3:8b-32k）──
    log("Layer 1: 初筛分析...")
    signals = summary.get("signals", [])
    if not signals:
        log("无信号，跳过", "WARN")
        return {"status": "skipped", "reason": "No signals"}

    # 取 top 50 信号给 L1
    top_signals = sorted(signals, key=lambda s: (
        1 if s.get("rsi_sig") == "超卖" else 0,
        1 if s.get("macd_sig") == "金叉" else 0,
        1 if s.get("ma_cross") == "多头" else 0,
    ), reverse=True)[:50]

    signal_text = "\n".join(
        f"{i+1}. {s['ts_code']} {s['name']} "
        f"收盘{s['close']} 涨跌{s['pct_chg']}% "
        f"MA:{s['ma_cross']} RSI:{s['rsi14']}({s['rsi_sig']}) "
        f"MACD:{s['macd_sig']} KDJ:{s['kdj_sig']} "
        f"量比:{s['vol_ratio']}"
        for i, s in enumerate(top_signals)
    )

    l1_prompt = f"""你是A股市场初筛分析师。以下是今日技术指标触发信号的{len(top_signals)}只股票：

{signal_text}

请完成以下任务：
1. 按照信号强度和质量，筛选出最值得关注的 10-15 只股票。
2. 对每只给出 1-2 句理由（基于技术指标共振）。
3. 输出格式：每行一只股票，格式为 "代码 名称 | 理由"

请直接输出，不要额外解释。"""

    l1_result = ollama_generate(l1_prompt, model_l1)
    if not l1_result:
        log("Layer 1 分析失败", "ERR")
        return {"status": "failed", "layer": 1}

    log(f"Layer 1 完成 ({len(l1_result)} 字符)", "OK")

    # ── Layer 2: 深度分析（deepseek-r1:70b）──
    # 从 L1 结果中提取代码
    import re
    extracted_codes = re.findall(r'(\d{6})', l1_result)
    if not extracted_codes:
        extracted_codes = [s['ts_code'].split('.')[0] for s in top_signals[:10]]

    log(f"Layer 2: 深度分析 {len(set(extracted_codes))} 只候选...")

    # 获取详细指标
    detailed_text = ""
    for code in list(set(extracted_codes))[:10]:
        cur = conn.cursor()
        cur.execute("SELECT name FROM stock_list WHERE ts_code LIKE ?", (f"{code}%",))
        nr = cur.fetchone()
        name = nr[0] if nr else code

        cur.execute("""
            SELECT calc_date, ma5, ma10, ma20, ma60, rsi6, rsi14,
                   macd_dif, macd_dea, macd_bar, kdj_k, kdj_d, kdj_j,
                   boll_upper, boll_lower, atr14, vol_ratio_5
            FROM indicators WHERE ts_code LIKE ? ORDER BY calc_date DESC LIMIT 5
        """, (f"{code}%",))
        rows = cur.fetchall()

        detailed_text += f"\n{code} {name}:\n"
        for r in rows:
            detailed_text += (f"  日期:{r[0]} MA5:{r[1]} MA10:{r[2]} MA20:{r[3]} "
                              f"RSI14:{r[6]} MACD_DIF:{r[7]:.3f} KDJ_K:{r[10]} "
                              f"BOLL:({r[13]},{r[14]}) ATR:{r[15]} 量比:{r[16]}\n")

    l2_prompt = f"""你是资深A股技术分析师。以下是经过初筛的候选股票详细技术指标：

{detailed_text}

请从3个维度给出深度分析：
1. **趋势质量**：均线排列是否健康？价格相对于关键均线的位置？
2. **动量共振**：RSI/MACD/KDJ 是否形成多指标共振？
3. **风险收益比**：基于布林带和ATR，当前入场的安全边际如何？

最后给出**精选推荐列表**（3-5只），附操作建议（买入区间、止损位、目标位）。

请用中文，结论先行。"""

    l2_result = ollama_generate(l2_prompt, model_l2, timeout=600)
    if not l2_result:
        log("Layer 2 分析失败，使用 L1 结果", "WARN")
        l2_result = l1_result
    else:
        log(f"Layer 2 完成 ({len(l2_result)} 字符)", "OK")

    # ── Layer 3: 精选研报 ──
    log("Layer 3: 生成精选研报...")
    l3_prompt = f"""你是A股首席策略分析师。基于以下深度分析结果，撰写一份专业的每日策略研报：

分析数据：
{l2_result[:3000]}

市场整体情况：
- 多头排列股票: {summary.get('ma_bullish', 'N/A')} 只
- 超卖信号: {summary.get('rsi_oversold', 'N/A')} 只
- MACD金叉: {summary.get('macd_golden', 'N/A')} 只

请撰写一份结构化的策略研报，包含：
1. **市场情绪研判**（一句话总结当前市场状态）
2. **核心策略**（今天的操作主题和方向）
3. **精选股票池**（3-5只，含具体操作建议）
4. **风险警示**（需要注意的风险点）

用中文，专业但易懂。"""

    l3_result = ollama_generate(l3_prompt, model_l3, timeout=600)
    if l3_result:
        log(f"Layer 3 完成 ({len(l3_result)} 字符)", "OK")
    else:
        log("Layer 3 失败，使用 L2 结果", "WARN")
        l3_result = l2_result

    return {
        "status": "completed",
        "l1_result": l1_result,
        "l2_result": l2_result,
        "l3_result": l3_result,
        "l1_model": model_l1,
        "l2_model": model_l2,
        "l3_model": model_l3,
    }


# ═══════════════════════════════════════════════════════
# Step 5: 生成HTML报告
# ═══════════════════════════════════════════════════════
def generate_html_report(summary, ai_result=None):
    """生成每日分析HTML报告"""
    log_step(5, 5, "生成HTML分析报告")

    report_date = summary.get("date", datetime.now().strftime("%Y%m%d"))
    gen_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── 信号排序 ──
    signals = sorted(summary.get("signals", []),
                     key=lambda s: (
                         1 if s.get("rsi_sig") == "超卖" else 0,
                         1 if s.get("macd_sig") == "金叉" else 0,
                         1 if s.get("ma_cross") == "多头" else 0,
                     ), reverse=True)

    # 信号统计
    total = summary.get("total_stocks", 0)
    ma_bull = summary.get("ma_bullish", 0)
    ma_bear = summary.get("ma_bearish", 0)
    rsi_os = summary.get("rsi_oversold", 0)
    rsi_ob = summary.get("rsi_overbought", 0)
    macd_g = summary.get("macd_golden", 0)
    macd_d = summary.get("macd_dead", 0)
    kdj_os = summary.get("kdj_oversold", 0)
    kdj_ob = summary.get("kdj_overbought", 0)
    vol_up = summary.get("vol_surge", 0)

    # 信号占比
    pct_bull = ma_bull / total * 100 if total else 0
    pct_bear = ma_bear / total * 100 if total else 0
    pct_os = rsi_os / total * 100 if total else 0
    pct_ob = rsi_ob / total * 100 if total else 0

    # 市场情绪判断
    if pct_bull > 60:
        mood = "强势"
        mood_emoji = "🔥"
        mood_color = "#e74c3c"
    elif pct_bull > 40:
        mood = "偏多"
        mood_emoji = "📈"
        mood_color = "#e67e22"
    elif pct_bear > 60:
        mood = "弱势"
        mood_emoji = "❄️"
        mood_color = "#27ae60"
    elif pct_bear > 40:
        mood = "偏空"
        mood_emoji = "📉"
        mood_color = "#2ecc71"
    else:
        mood = "震荡"
        mood_emoji = "🔄"
        mood_color = "#7f8c8d"

    # ── 构建HTML ──
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A股每日技术分析报告 — {report_date}</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif; background:#f5f6fa; color:#2c3e50; }}
.header {{ background:linear-gradient(135deg,#1a1a2e,#16213e); color:#fff; padding:40px 20px; text-align:center; }}
.header h1 {{ font-size:28px; margin-bottom:8px; }}
.header .subtitle {{ color:#a0aec0; font-size:14px; }}
.container {{ max-width:1200px; margin:0 auto; padding:20px; }}

/* 概览卡片 */
.overview {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:16px; margin-bottom:30px; }}
.card {{ background:#fff; border-radius:12px; padding:20px; box-shadow:0 2px 8px rgba(0,0,0,0.06); text-align:center; }}
.card .label {{ font-size:13px; color:#7f8c8d; margin-bottom:8px; }}
.card .value {{ font-size:32px; font-weight:700; }}
.card .sub {{ font-size:12px; color:#95a5a6; margin-top:4px; }}

/* 市场情绪 */
.mood-card {{ background:#fff; border-radius:12px; padding:24px; box-shadow:0 2px 8px rgba(0,0,0,0.06); margin-bottom:30px; text-align:center; }}
.mood-card .mood-emoji {{ font-size:48px; }}
.mood-card .mood-text {{ font-size:24px; font-weight:700; color:{mood_color}; margin:8px 0; }}
.mood-card .mood-desc {{ font-size:14px; color:#7f8c8d; }}

/* 指标分布 */
.indicators-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:16px; margin-bottom:30px; }}
.indicator-card {{ background:#fff; border-radius:12px; padding:20px; box-shadow:0 2px 8px rgba(0,0,0,0.06); }}
.indicator-card h3 {{ font-size:14px; color:#7f8c8d; margin-bottom:12px; border-bottom:1px solid #ecf0f1; padding-bottom:8px; }}
.bar-row {{ display:flex; align-items:center; margin:8px 0; }}
.bar-label {{ width:80px; font-size:13px; color:#2c3e50; }}
.bar-track {{ flex:1; height:20px; background:#ecf0f1; border-radius:10px; overflow:hidden; }}
.bar-fill {{ height:100%; border-radius:10px; display:flex; align-items:center; justify-content:flex-end; padding-right:8px; font-size:11px; color:#fff; font-weight:600; }}
.bar-count {{ width:60px; text-align:right; font-size:13px; color:#7f8c8d; margin-left:8px; }}

/* 信号表格 */
.signal-section {{ background:#fff; border-radius:12px; padding:20px; box-shadow:0 2px 8px rgba(0,0,0,0.06); margin-bottom:30px; }}
.signal-section h2 {{ font-size:18px; margin-bottom:16px; color:#2c3e50; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th {{ background:#f8f9fa; padding:10px 12px; text-align:left; font-weight:600; color:#7f8c8d; border-bottom:2px solid #dee2e6; position:sticky; top:0; }}
td {{ padding:10px 12px; border-bottom:1px solid #f0f0f0; }}
tr:hover {{ background:#f8f9ff; }}
.badge {{ display:inline-block; padding:2px 8px; border-radius:10px; font-size:11px; font-weight:600; }}
.badge-up {{ background:#ffeaea; color:#e74c3c; }}
.badge-down {{ background:#eafaf1; color:#27ae60; }}
.badge-golden {{ background:#fff3e0; color:#e67e22; }}
.badge-dead {{ background:#e8f5e9; color:#2e7d32; }}
.badge-oversold {{ background:#e3f2fd; color:#1565c0; }}
.badge-overbought {{ background:#fce4ec; color:#c62828; }}
.badge-neutral {{ background:#f5f5f5; color:#9e9e9e; }}
.badge-bull {{ background:#ffeaea; color:#e74c3c; }}
.badge-bear {{ background:#eafaf1; color:#27ae60; }}
.pct-up {{ color:#e74c3c; font-weight:600; }}
.pct-down {{ color:#27ae60; font-weight:600; }}

/* AI分析 */
.ai-section {{ background:#fff; border-radius:12px; padding:24px; box-shadow:0 2px 8px rgba(0,0,0,0.06); margin-bottom:30px; }}
.ai-section h2 {{ font-size:18px; margin-bottom:16px; color:#2c3e50; }}
.ai-content {{ background:#f8f9fa; border-radius:8px; padding:20px; font-size:14px; line-height:1.8; white-space:pre-wrap; color:#2c3e50; }}
.ai-layer {{ margin-bottom:24px; }}
.ai-layer h3 {{ font-size:15px; color:#7f8c8d; margin-bottom:8px; padding:4px 12px; border-left:3px solid #3498db; }}

.footer {{ text-align:center; padding:30px; color:#95a5a6; font-size:12px; }}

@media (max-width:768px) {{
    .overview {{ grid-template-columns:repeat(2,1fr); }}
    table {{ font-size:12px; }}
    th, td {{ padding:6px 8px; }}
}}
</style>
</head>
<body>

<div class="header">
    <h1>📊 A股每日技术分析报告</h1>
    <div class="subtitle">分析日期: {report_date} | 生成时间: {gen_time} | 覆盖: {total} 只股票</div>
</div>

<div class="container">

    <!-- 市场情绪 -->
    <div class="mood-card">
        <div class="mood-emoji">{mood_emoji}</div>
        <div class="mood-text">{mood}市场</div>
        <div class="mood-desc">
            多头排列 {ma_bull} 只 ({pct_bull:.1f}%) |
            空头排列 {ma_bear} 只 ({pct_bear:.1f}%) |
            超卖信号 {rsi_os} 只 ({pct_os:.1f}%)
        </div>
    </div>

    <!-- 核心概览 -->
    <div class="overview">
        <div class="card">
            <div class="label">📈 多头排列</div>
            <div class="value" style="color:#e74c3c">{ma_bull}</div>
            <div class="sub">{pct_bull:.1f}%</div>
        </div>
        <div class="card">
            <div class="label">📉 空头排列</div>
            <div class="value" style="color:#27ae60">{ma_bear}</div>
            <div class="sub">{pct_bear:.1f}%</div>
        </div>
        <div class="card">
            <div class="label">🔵 RSI超卖</div>
            <div class="value" style="color:#3498db">{rsi_os}</div>
            <div class="sub">{pct_os:.1f}%</div>
        </div>
        <div class="card">
            <div class="label">🔴 RSI超买</div>
            <div class="value" style="color:#e74c3c">{rsi_ob}</div>
            <div class="sub">{pct_ob:.1f}%</div>
        </div>
        <div class="card">
            <div class="label">🟡 MACD金叉</div>
            <div class="value" style="color:#e67e22">{macd_g}</div>
            <div class="sub">{macd_g/total*100:.1f}%</div>
        </div>
    </div>

    <!-- 指标分布 -->
    <div class="indicators-grid">
        <div class="indicator-card">
            <h3>📈 均线形态分布</h3>
            <div class="bar-row">
                <span class="bar-label">多头</span>
                <div class="bar-track"><div class="bar-fill" style="width:{min(pct_bull,100)}%;background:#e74c3c">{pct_bull:.0f}%</div></div>
                <span class="bar-count">{ma_bull}</span>
            </div>
            <div class="bar-row">
                <span class="bar-label">空头</span>
                <div class="bar-track"><div class="bar-fill" style="width:{min(pct_bear,100)}%;background:#27ae60">{pct_bear:.0f}%</div></div>
                <span class="bar-count">{ma_bear}</span>
            </div>
            <div class="bar-row">
                <span class="bar-label">缠绕</span>
                <div class="bar-track"><div class="bar-fill" style="width:{min(100-pct_bull-pct_bear,100)}%;background:#95a5a6">{(100-pct_bull-pct_bear):.0f}%</div></div>
                <span class="bar-count">{total-ma_bull-ma_bear}</span>
            </div>
        </div>
        <div class="indicator-card">
            <h3>📊 RSI信号分布</h3>
            <div class="bar-row">
                <span class="bar-label">超卖</span>
                <div class="bar-track"><div class="bar-fill" style="width:{min(pct_os,100)}%;background:#3498db">{pct_os:.0f}%</div></div>
                <span class="bar-count">{rsi_os}</span>
            </div>
            <div class="bar-row">
                <span class="bar-label">超买</span>
                <div class="bar-track"><div class="bar-fill" style="width:{min(pct_ob,100)}%;background:#e74c3c">{pct_ob:.0f}%</div></div>
                <span class="bar-count">{rsi_ob}</span>
            </div>
            <div class="bar-row">
                <span class="bar-label">放量</span>
                <div class="bar-track"><div class="bar-fill" style="width:{min(vol_up/total*100,100)}%;background:#e67e22">{vol_up/total*100:.0f}%</div></div>
                <span class="bar-count">{vol_up}</span>
            </div>
        </div>
    </div>
"""

    # ── AI 分析部分 ──
    if ai_result and ai_result.get("status") == "completed":
        html += """
    <!-- AI 策略分析 -->
    <div class="ai-section">
        <h2>🤖 AI 策略分析</h2>
"""
        if ai_result.get("l3_result"):
            html += f"""
        <div class="ai-layer">
            <h3>📋 精选研报 (Layer 3)</h3>
            <div class="ai-content">{ai_result['l3_result']}</div>
        </div>
"""
        if ai_result.get("l2_result"):
            html += f"""
        <div class="ai-layer">
            <h3>🔍 深度分析 (Layer 2 — {ai_result.get('l2_model', 'N/A')})</h3>
            <div class="ai-content">{ai_result['l2_result']}</div>
        </div>
"""
        if ai_result.get("l1_result"):
            html += f"""
        <div class="ai-layer">
            <h3>⚡ 初筛 (Layer 1 — {ai_result.get('l1_model', 'N/A')})</h3>
            <div class="ai-content">{ai_result['l1_result']}</div>
        </div>
"""
        html += "    </div>\n"
    elif ai_result:
        status = ai_result.get("status", "unknown")
        reason = ai_result.get("reason", "")
        html += f"""
    <!-- AI 分析状态 -->
    <div class="mood-card">
        <div class="mood-emoji">⏭️</div>
        <div class="mood-text" style="color:#7f8c8d;">AI 分析已跳过</div>
        <div class="mood-desc">状态: {status} | {reason}</div>
    </div>
"""
    else:
        html += """
    <!-- AI 分析状态 -->
    <div class="mood-card">
        <div class="mood-emoji">⏭️</div>
        <div class="mood-text" style="color:#7f8c8d;">AI 分析未执行</div>
        <div class="mood-desc">使用 --skip-ollama 跳过了 AI 分析</div>
    </div>
"""

    # ── 信号表格 ──
    html += f"""
    <!-- 技术信号列表 -->
    <div class="signal-section">
        <h2>🔔 技术信号列表（{len(signals)} 条）</h2>
        <div style="overflow-x:auto;">
        <table>
            <thead>
                <tr>
                    <th>代码</th>
                    <th>名称</th>
                    <th>行业</th>
                    <th>收盘价</th>
                    <th>涨跌幅</th>
                    <th>均线</th>
                    <th>RSI(14)</th>
                    <th>RSI信号</th>
                    <th>MACD</th>
                    <th>KDJ</th>
                    <th>量比(5日)</th>
                </tr>
            </thead>
            <tbody>
"""
    for s in signals[:200]:  # 最多显示200条
        ts_code = s.get('ts_code', '')
        name = s.get('name', '')
        industry = s.get('industry', '')
        close = s.get('close', '')
        pct = s.get('pct_chg', 0)

        # 涨跌幅样式
        pct_class = "pct-up" if (pct and pct > 0) else "pct-down" if (pct and pct < 0) else ""
        pct_str = f"+{pct}%" if pct and pct > 0 else f"{pct}%" if pct else ""

        # 信号Badge
        ma_badge = {"多头": "badge-bull", "空头": "badge-bear"}.get(
            s.get('ma_cross'), "badge-neutral")
        rsi_badge = {"超卖": "badge-oversold", "超买": "badge-overbought"}.get(
            s.get('rsi_sig'), "badge-neutral")
        macd_badge = {"金叉": "badge-golden", "死叉": "badge-dead"}.get(
            s.get('macd_sig'), "badge-neutral")
        kdj_badge = {"超卖": "badge-oversold", "超买": "badge-overbought",
                     "金叉": "badge-golden", "死叉": "badge-dead"}.get(
            s.get('kdj_sig'), "badge-neutral")

        html += f"""
                <tr>
                    <td><code>{ts_code}</code></td>
                    <td>{name}</td>
                    <td style="color:#7f8c8d;font-size:12px">{industry}</td>
                    <td>{close}</td>
                    <td class="{pct_class}">{pct_str}</td>
                    <td><span class="badge {ma_badge}">{s.get('ma_cross', '-')}</span></td>
                    <td>{s.get('rsi14', '-')}</td>
                    <td><span class="badge {rsi_badge}">{s.get('rsi_sig', '-')}</span></td>
                    <td><span class="badge {macd_badge}">{s.get('macd_sig', '-')}</span></td>
                    <td><span class="badge {kdj_badge}">{s.get('kdj_sig', '-')}</span></td>
                    <td>{s.get('vol_ratio', '-')}</td>
                </tr>"""

    html += f"""
            </tbody>
        </table>
        </div>
        {"<p style='text-align:center;color:#95a5a6;margin-top:12px'>仅显示前200条信号，完整数据见数据库 indicators 表</p>" if len(signals) > 200 else ""}
    </div>

    <div class="footer">
        <p>A股每日自动化分析流水线 | 泓锦 AI 搭档</p>
        <p>数据来源: akshare | 分析引擎: Python + 本地数据库 | 生成时间: {gen_time}</p>
        <p style="margin-top:8px;font-size:11px;color:#bdc3c7">
            免责声明: 本报告仅为技术分析参考，不构成任何投资建议。股市有风险，投资需谨慎。
        </p>
    </div>

</div>
</body>
</html>"""

    # 保存报告
    report_path = os.path.join(REPORT_DIR, f"daily_report_{report_date}.html")
    os.makedirs(REPORT_DIR, exist_ok=True)

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html)

    # 同时保存一份 latest.html
    latest_path = os.path.join(REPORT_DIR, "latest.html")
    with open(latest_path, "w", encoding="utf-8") as f:
        f.write(html)

    log(f"报告已保存: {report_path}", "OK")
    return report_path


# ═══════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        description="A股每日自动化分析流水线",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python automation/daily_pipeline.py                  # 完整5步流程
  python automation/daily_pipeline.py --skip-ollama     # 跳过AI分析
  python automation/daily_pipeline.py --step 3          # 只执行第3步(指标计算)
  python automation/daily_pipeline.py --days 10         # 只更新最近10天
        """
    )
    parser.add_argument("--skip-ollama", action="store_true",
                        help="跳过 Ollama AI 分析（Layer 1-3）")
    parser.add_argument("--step", type=int, choices=[1, 2, 3, 4, 5],
                        help="只执行指定步骤")
    parser.add_argument("--days", type=int, default=30,
                        help="K线数据补全天数（默认30天）")
    parser.add_argument("--max-stocks", type=int, default=None,
                        help="最大计算股票数（调试用）")
    args = parser.parse_args()

    # 打印启动信息
    print("=" * 60)
    print("  🚀 A股每日自动化分析流水线")
    print(f"  启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  参数: --skip-ollama={args.skip_ollama} --days={args.days}")
    if args.step:
        print(f"  模式: 单步执行 (Step {args.step})")
    print("=" * 60)

    total_start = time.time()
    summary = None
    ai_result = None

    # ── 执行流水线 ──
    conn = None

    try:
        # Step 1: 元数据更新
        if not args.step or args.step == 1:
            stock_count = update_stock_metadata()
            if args.step == 1:
                return

        # Step 2: K线增量下载
        if not args.step or args.step == 2:
            new_klines = update_daily_klines(days=args.days)
            if args.step == 2:
                return

        # Step 3: 批量计算技术指标
        if not args.step or args.step == 3:
            summary = batch_calculate_indicators(max_stocks=args.max_stocks)
            if args.step == 3:
                return

        # Step 4: AI分析
        if not args.step or args.step == 4:
            if args.skip_ollama:
                log("已跳过 AI 分析 (--skip-ollama)", "WARN")
                ai_result = {"status": "skipped", "reason": "--skip-ollama flag"}
            else:
                conn = sqlite3.connect(DB_PATH)
                ai_result = ollama_three_layer_analysis(summary, conn)
                if args.step == 4:
                    return

        # Step 5: 生成HTML报告
        if not args.step or args.step == 5:
            if summary is None:
                log("缺少分析数据，无法生成报告", "ERR")
                return
            report_path = generate_html_report(summary, ai_result)
            if args.step == 5:
                return

    except KeyboardInterrupt:
        log("用户中断", "WARN")
        sys.exit(1)
    except Exception as e:
        log(f"流水线异常: {e}", "ERR")
        traceback.print_exc()
        sys.exit(1)
    finally:
        if conn:
            conn.close()

    # ── 完成总结 ──
    elapsed = time.time() - total_start
    mins = int(elapsed // 60)
    secs = int(elapsed % 60)

    print("\n" + "=" * 60)
    print(f"  ✅ 流水线完成！")
    print(f"  总耗时: {mins}分{secs}秒")
    print(f"  报告路径: ~/stock-data/reports/latest.html")
    if summary:
        print(f"  覆盖股票: {summary.get('total_stocks', 'N/A')} 只")
        print(f"  信号数量: {len(summary.get('signals', []))} 条")
    if ai_result:
        print(f"  AI分析: {ai_result.get('status', 'N/A')}")
    print("=" * 60)


if __name__ == "__main__":
    main()
