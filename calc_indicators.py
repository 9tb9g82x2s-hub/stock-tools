#!/usr/bin/env python3
"""
为 stk_factor 表补全价格数据 + 技术指标
- 从 daily 表 JOIN 价格列（open/high/low/close/vol等）
- 逐股计算 MACD/KDJ/RSI/BOLL/CCI
- 只处理指标为 NULL 的行（增量）
"""
import sqlite3, numpy as np, time
from datetime import datetime

DB = '/Users/ziruzhu/stock-data/stock_all.db'

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

conn = sqlite3.connect(DB)
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA cache_size=-100000')  # 100MB cache
cur = conn.cursor()

log("===== 补全 stk_factor 技术指标 =====")

# ── 1. 从 daily 表补 price 字段 ─────────────────────────────────────
log("1. 补价格数据（从daily表JOIN）")
cur.execute("""
    UPDATE stk_factor SET
        open     = (SELECT d.open     FROM daily d WHERE d.ts_code=stk_factor.ts_code AND d.trade_date=stk_factor.trade_date),
        high     = (SELECT d.high     FROM daily d WHERE d.ts_code=stk_factor.ts_code AND d.trade_date=stk_factor.trade_date),
        low      = (SELECT d.low      FROM daily d WHERE d.ts_code=stk_factor.ts_code AND d.trade_date=stk_factor.trade_date),
        close    = (SELECT d.close    FROM daily d WHERE d.ts_code=stk_factor.ts_code AND d.trade_date=stk_factor.trade_date),
        vol      = (SELECT d.vol      FROM daily d WHERE d.ts_code=stk_factor.ts_code AND d.trade_date=stk_factor.trade_date),
        amount   = (SELECT d.amount   FROM daily d WHERE d.ts_code=stk_factor.ts_code AND d.trade_date=stk_factor.trade_date),
        pre_close= (SELECT d.pre_close FROM daily d WHERE d.ts_code=stk_factor.ts_code AND d.trade_date=stk_factor.trade_date),
        change   = (SELECT d.change   FROM daily d WHERE d.ts_code=stk_factor.ts_code AND d.trade_date=stk_factor.trade_date),
        pct_change=(SELECT d.pct_chg  FROM daily d WHERE d.ts_code=stk_factor.ts_code AND d.trade_date=stk_factor.trade_date)
    WHERE open IS NULL
""")
conn.commit()
cur.execute("SELECT COUNT(*) FROM stk_factor WHERE open IS NULL")
log(f"  剩余open为NULL: {cur.fetchone()[0]} 行")

# ── 2. 获取所有需要计算指标的股票 ─────────────────────────────────────
cur.execute("SELECT DISTINCT ts_code FROM stk_factor WHERE macd_dif IS NULL ORDER BY ts_code")
codes = [r[0] for r in cur.fetchall()]
log(f"2. 需要计算指标的股票: {len(codes)} 只")

if not codes:
    log("  无需计算，全部完成")
    conn.close()
    exit(0)

# ── 3. 逐股计算指标 ─────────────────────────────────────────────────
log("3. 计算技术指标...")
total_updated = 0

for idx, code in enumerate(codes):
    cur.execute("""
        SELECT trade_date, close, high, low, vol FROM stk_factor
        WHERE ts_code=? ORDER BY trade_date
    """, [code])
    rows = cur.fetchall()
    if len(rows) < 30:
        continue  # 数据不足

    dates = [r[0] for r in rows]
    closes  = np.array([r[1] or 0 for r in rows], dtype=float)
    highs   = np.array([r[2] or 0 for r in rows], dtype=float)
    lows    = np.array([r[3] or 0 for r in rows], dtype=float)
    vols    = np.array([r[4] or 0 for r in rows], dtype=float)

    n = len(closes)

    # MACD (12,26,9)
    ema12 = np.zeros(n); ema26 = np.zeros(n); dif = np.zeros(n); dea = np.zeros(n); macd = np.zeros(n)
    ema12[0] = closes[0]; ema26[0] = closes[0]
    for i in range(1, n):
        ema12[i] = closes[i] * 2/(12+1) + ema12[i-1] * (1 - 2/(12+1))
        ema26[i] = closes[i] * 2/(26+1) + ema26[i-1] * (1 - 2/(26+1))
        dif[i] = ema12[i] - ema26[i]
    for i in range(1, n):
        dea[i] = dea[i-1] + (dif[i] - dea[i-1]) * 2/(9+1)
        macd[i] = (dif[i] - dea[i]) * 2

    # KDJ (9,3,3)
    k = np.zeros(n); d = np.zeros(n); j = np.zeros(n)
    k[0] = 50; d[0] = 50
    for i in range(8, n):
        hh = np.max(highs[max(0,i-8):i+1])
        ll = np.min(lows[max(0,i-8):i+1])
        rsv = (closes[i] - ll) / (hh - ll) * 100 if hh != ll else 50
        k[i] = k[i-1] * 2/3 + rsv * 1/3
        d[i] = d[i-1] * 2/3 + k[i] * 1/3
        j[i] = 3 * k[i] - 2 * d[i]

    # RSI (6,12,24) - Wilder's Smoothing
    def calc_rsi(closes, period):
        n = len(closes)
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        rsi = np.full(n, np.nan)
        if n <= period:
            return rsi
        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])
        for i in range(period, n):
            if i == period:
                avg_gain = np.mean(gains[:period])
                avg_loss = np.mean(losses[:period])
            else:
                avg_gain = (avg_gain * (period-1) + gains[i-1]) / period
                avg_loss = (avg_loss * (period-1) + losses[i-1]) / period
            rs = avg_gain / max(avg_loss, 0.0001)
            rsi[i] = 100 - 100 / (1 + rs)
        return rsi
    rsi6 = calc_rsi(closes, 6)
    rsi12v = calc_rsi(closes, 12)
    rsi24v = calc_rsi(closes, 24)

    # BOLL (20,2)
    boll_upper = np.zeros(n); boll_mid = np.zeros(n); boll_lower = np.zeros(n)
    for i in range(19, n):
        window = closes[i-19:i+1]
        ma = np.mean(window)
        std = np.std(window)
        boll_mid[i] = ma
        boll_upper[i] = ma + 2 * std
        boll_lower[i] = ma - 2 * std

    # CCI (14)
    cci = np.zeros(n)
    tp = (highs + lows + closes) / 3
    for i in range(13, n):
        ma_tp = np.mean(tp[i-13:i+1])
        md = np.mean(np.abs(tp[i-13:i+1] - ma_tp))
        cci[i] = (tp[i] - ma_tp) / (0.015 * max(md, 0.0001))

    # 更新数据库（只更新NULL的行）
    update = 0
    for i in range(n):
        if i < 20:  # 前20行指标不稳，跳过
            continue
        cur.execute("SELECT macd_dif FROM stk_factor WHERE ts_code=? AND trade_date=?",
                    [code, dates[i]])
        existing = cur.fetchone()
        if not existing or existing[0] is not None:
            continue  # 已有指标，跳过
        
        cur.execute("""UPDATE stk_factor SET
            macd_dif=?, macd_dea=?, macd=?,
            kdj_k=?, kdj_d=?, kdj_j=?,
            rsi_6=?, rsi_12=?, rsi_24=?,
            boll_upper=?, boll_mid=?, boll_lower=?, cci=?
            WHERE ts_code=? AND trade_date=?""",
            [float(dif[i]), float(dea[i]), float(macd[i]),
             float(k[i]), float(d[i]), float(j[i]),
             float(rsi6[i]), float(rsi12v[i]), float(rsi24v[i]),
             float(boll_upper[i]), float(boll_mid[i]), float(boll_lower[i]), float(cci[i]),
             code, dates[i]])
        update += 1

    if update > 0:
        total_updated += update
        if (idx+1) % 50 == 0:
            conn.commit()
            log(f"  进度: {idx+1}/{len(codes)} 只，已更新 {total_updated} 行")

conn.commit()
log(f"3. 指标计算完成，共更新 {total_updated} 行")

# ── 4. 复权价格（简单用 adj_factor 乘除）───────────────────────────
log("4. 补复权价格...")
cur.execute("""
    UPDATE stk_factor SET
        open_hfq       = open * adj_factor,
        open_qfq       = open * adj_factor,
        close_hfq      = close * adj_factor,
        close_qfq      = close * adj_factor,
        high_hfq       = high * adj_factor,
        high_qfq       = high * adj_factor,
        low_hfq        = low * adj_factor,
        low_qfq        = low * adj_factor,
        pre_close_hfq  = pre_close * adj_factor,
        pre_close_qfq  = pre_close * adj_factor
    WHERE open_hfq IS NULL AND open IS NOT NULL AND adj_factor IS NOT NULL
""")
conn.commit()
cur.execute("SELECT COUNT(*) FROM stk_factor WHERE macd_dif IS NULL")
remaining = cur.fetchone()[0]
log(f"✅ 剩余NULL指标: {remaining} 行（前20行无指标属正常）")

conn.close()
log("===== 完成 =====")
