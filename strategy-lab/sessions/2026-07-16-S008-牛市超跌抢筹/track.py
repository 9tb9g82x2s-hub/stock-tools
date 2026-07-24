#!/usr/bin/env python3
"""
S008 策略追踪系统
用法:
  python3 track.py                    # 扫描+通知新信号+更新已持仓
  python3 track.py --add 300502.SZ    # 手动入场某只股票
  python3 track.py --add 300502.SZ,688256.SH  # 批量入场
  python3 track.py --report           # 查看持仓报告
  python3 track.py --init             # 初始化数据库
"""
import sqlite3, pandas as pd, numpy as np, os, time, sys
from datetime import datetime

DB_TRACK = os.path.expanduser('~/stock-data/s008_track.db')
DB_STOCK = os.path.expanduser('~/stock-data/stock_all.db')

def init_db():
    conn = sqlite3.connect(DB_TRACK)
    conn.execute('''CREATE TABLE IF NOT EXISTS positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts_code TEXT NOT NULL,
        name TEXT,
        entry_date TEXT NOT NULL,
        entry_price REAL NOT NULL,
        exit_date TEXT,
        exit_price REAL,
        status TEXT DEFAULT 'holding',
        holding_days INTEGER DEFAULT 0,
        return_pct REAL,
        peak_return REAL DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now','localtime'))
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS nav (
        date TEXT PRIMARY KEY, total_value REAL, cash REAL,
        positions_value REAL, n_positions INTEGER, total_return_pct REAL
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS scan_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        scan_date TEXT NOT NULL, bull_status TEXT,
        new_signals INTEGER, brief TEXT
    )''')
    conn.commit(); conn.close()
    print("✅ 数据库已初始化")

def is_bull_market():
    import akshare as ak
    idx = ak.stock_zh_index_daily(symbol='sh000300')
    idx['date'] = pd.to_datetime(idx['date'])
    idx['ma200'] = idx['close'].rolling(200).mean()
    bull = idx.iloc[-1]['close'] > idx.iloc[-1]['ma200']
    return bull, idx.iloc[-1]['close'], idx.iloc[-1]['ma200']

def scan():
    """扫描当前符合条件的股票，返回信号列表"""
    conn = sqlite3.connect(DB_STOCK)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT ts_code FROM stock_list WHERE name NOT LIKE '%ST%' AND name NOT LIKE '%*ST%' AND name NOT LIKE '%退%'")
    clean = set(r[0] for r in cur.fetchall())
    cur.execute("""SELECT ts_code FROM daily WHERE trade_date>='20240101'
        GROUP BY ts_code ORDER BY AVG(CAST(vol AS REAL)*CAST(close AS REAL)) DESC LIMIT 1000""")
    pool = [r[0] for r in cur.fetchall() if r[0] in clean]
    cs = ','.join(f"'{c}'" for c in pool)
    
    daily = pd.read_sql(f"""SELECT ts_code, trade_date,
        CAST(open AS REAL) as o, CAST(close AS REAL) as c,
        CAST(high AS REAL) as h, CAST(low AS REAL) as l, CAST(vol AS REAL) as v
        FROM daily WHERE ts_code IN ({cs}) AND trade_date>='20240101'
        ORDER BY ts_code, trade_date""", conn)
    daily['trade_date'] = pd.to_datetime(daily['trade_date'], format='%Y%m%d')
    
    names = {}
    for r in cur.execute("SELECT ts_code, name FROM stock_list"):
        if r[0] in pool: names[r[0]] = r[1]
    conn.close()
    
    signals = []
    for tc in pool:
        sd = daily[daily['ts_code']==tc].sort_values('trade_date').reset_index(drop=True)
        if len(sd) < 150: continue
        c = sd['c'].values; h = sd['h'].values; l = sd['l'].values
        v = sd['v'].values; dates = list(sd['trade_date'])
        i = len(c) - 1
        
        lookback = min(120, i)
        peak_idx = i - lookback + np.argmax(h[i-lookback:i+1])
        decline_days = i - peak_idx
        dd = c[i] / c[peak_idx] - 1
        if decline_days < 20 or decline_days >= 100: continue
        if dd > -0.25: continue
        
        daily_drop = dd / decline_days * 100
        if daily_drop > -0.4: continue
        
        if i < 25: continue
        recent_5 = sd.iloc[max(0,i-5):i+1]
        pre_20 = sd.iloc[max(0,i-25):max(0,i-5)]
        recent_range = (recent_5['h'].max() - recent_5['l'].min()) / recent_5['c'].mean() * 100
        pre_range = (pre_20['h'].max() - pre_20['l'].min()) / pre_20['c'].mean() * 100 if len(pre_20)>0 else recent_range
        if pre_range <= 0: continue
        if recent_range / pre_range > 1.2: continue
        
        obv = np.zeros(len(c))
        for j in range(1, len(c)):
            if c[j] > c[j-1]: obv[j] = obv[j-1] + v[j]
            elif c[j] < c[j-1]: obv[j] = obv[j-1] - v[j]
            else: obv[j] = obv[j-1]
        if obv[max(0,i-5):i+1].min() <= obv[max(0,i-60):max(0,i-5)].min() * 1.01: continue
        
        # RSI用于辅助参考
        gain = np.where(np.diff(c, prepend=c[0])>0, np.diff(c, prepend=c[0]), 0)
        loss = np.where(np.diff(c, prepend=c[0])<0, -np.diff(c, prepend=c[0]), 0)
        avg_g = pd.Series(gain).rolling(14).mean().values
        avg_l = pd.Series(loss).rolling(14).mean().values
        rsi_val = 100 - 100 / (1 + avg_g / np.maximum(avg_l, 1e-10))
        ma20 = pd.Series(c).rolling(20).mean().values
        ma60 = pd.Series(c).rolling(60).mean().values
        vma20 = pd.Series(v).rolling(20).mean().values
        
        signals.append({
            'ts_code': tc, 'name': names.get(tc, tc),
            'price': float(c[i]),
            'dd_pct': float(dd*100), 'days': decline_days,
            'daily_drop': float(daily_drop),
            'shrink': float(recent_range/pre_range),
            'rsi': float(rsi_val[i]) if not np.isnan(rsi_val[i]) else 50,
            'dist_ma20': float((c[i]/ma20[i]-1)*100) if not np.isnan(ma20[i]) else 0,
            'dist_ma60': float((c[i]/ma60[i]-1)*100) if not np.isnan(ma60[i]) else 0,
            'vol_ratio': float(v[i]/vma20[i]) if vma20[i]>0 else 1,
            'peak_date': str(dates[peak_idx].date()),
        })
    
    signals.sort(key=lambda x: x['dd_pct'])
    return signals

def add_position(ts_code):
    """手动添加一只股票到持仓"""
    conn = sqlite3.connect(DB_STOCK)
    cur = conn.cursor()
    cur.execute("SELECT CAST(close AS REAL) FROM daily WHERE ts_code=? ORDER BY trade_date DESC LIMIT 1", (ts_code,))
    row = cur.fetchone()
    cur.execute("SELECT name FROM stock_list WHERE ts_code=?", (ts_code,))
    name_row = cur.fetchone()
    conn.close()
    
    if not row:
        print(f"❌ 找不到 {ts_code} 的价格数据")
        return
    
    conn = sqlite3.connect(DB_TRACK)
    existing = conn.execute("SELECT id FROM positions WHERE ts_code=? AND status='holding'", (ts_code,)).fetchone()
    if existing:
        print(f"⚠️ {ts_code} 已在持仓中")
        conn.close()
        return
    
    today = datetime.now().strftime('%Y-%m-%d')
    conn.execute("INSERT INTO positions (ts_code, name, entry_date, entry_price, status, holding_days) VALUES (?,?,?,?,'holding',0)",
        (ts_code, name_row[0] if name_row else ts_code, today, row[0]))
    conn.commit()
    conn.close()
    print(f"✅ {ts_code} {name_row[0] if name_row else ''} @ {row[0]:.2f} 已入场")

def update_positions():
    """更新已持仓的浮盈和退出"""
    conn = sqlite3.connect(DB_TRACK)
    today = datetime.now().strftime('%Y-%m-%d')
    
    conn.execute("""UPDATE positions SET holding_days = 
        CAST(julianday(?) - julianday(entry_date) AS INTEGER)
        WHERE status='holding'""", (today,))
    
    conn_stock = sqlite3.connect(DB_STOCK)
    cur = conn_stock.cursor()
    holdings = conn.execute("SELECT id, ts_code FROM positions WHERE status='holding'").fetchall()
    for pid, tc in holdings:
        cur.execute("SELECT CAST(close AS REAL) FROM daily WHERE ts_code=? ORDER BY trade_date DESC LIMIT 1", (tc,))
        row = cur.fetchone()
        if row:
            current = row[0]
            entry = conn.execute("SELECT entry_price FROM positions WHERE id=?", (pid,)).fetchone()[0]
            ret = (current / entry - 1) * 100
            conn.execute("UPDATE positions SET return_pct=?, peak_return=MAX(peak_return,?) WHERE id=?", (ret, ret, pid))
    conn_stock.close()
    
    exited = conn.execute("""UPDATE positions SET status='closed', exit_date=?
        WHERE status='holding' AND holding_days >= 60""", (today,)).rowcount
    
    conn.commit(); conn.close()
    return exited

def show_report():
    """显示持仓报告"""
    conn = sqlite3.connect(DB_TRACK)
    bull, hs300, ma200 = is_bull_market()
    today = datetime.now().strftime('%Y-%m-%d')
    
    print(f"\n{'='*60}")
    print(f"S008 持仓报告 — {today}")
    print(f"{'='*60}")
    print(f"市场: {'🐂 牛市' if bull else '🐻 熊市'} | 沪深300={hs300:.0f} MA200={ma200:.0f}")
    
    holdings = conn.execute("""SELECT ts_code, name, entry_date, entry_price, holding_days, return_pct, peak_return
        FROM positions WHERE status='holding' ORDER BY entry_date""").fetchall()
    
    print(f"\n📊 当前持仓: {len(holdings)}笔")
    if holdings:
        print(f"  {'代码':<12s} {'名称':<8s} {'入场日':<12s} {'入场价':>8s} {'持有':>5s} {'浮盈':>8s} {'峰值':>8s}")
        print("  " + "-" * 65)
        for h in holdings:
            color = '🔴' if h[5] and h[5] > 0 else '🟢' if h[5] and h[5] < 0 else '⚪'
            ret_str = f"{h[5]:+.1f}%" if h[5] is not None else '--'
            peak_str = f"{h[6]:+.1f}%" if h[6] is not None else '--'
            print(f"  {h[0]:<12s} {h[1]:<8s} {h[2]:<12s} {h[3]:>8.2f} {h[4]:>4d}天 {ret_str:>7s} {color} {peak_str:>7s}")
    
    closed = conn.execute("SELECT COUNT(*), COALESCE(AVG(return_pct),0), COALESCE(AVG(CASE WHEN return_pct>0 THEN 1 ELSE 0 END),0) FROM positions WHERE status='closed'").fetchone()
    if closed[0] > 0:
        print(f"\n📈 已平仓: {closed[0]}笔 | 均收益: {closed[1]:+.1f}% | 胜率: {closed[2]*100:.0f}%")
    
    conn.close()

def show_new_signals(signals):
    """展示新信号，等待老大决策"""
    existing = set()
    conn = sqlite3.connect(DB_TRACK)
    for r in conn.execute("SELECT ts_code FROM positions WHERE status='holding'"):
        existing.add(r[0])
    conn.close()
    
    new_sigs = [s for s in signals if s['ts_code'] not in existing]
    
    if not new_sigs:
        print("\n📭 无新增信号，所有候选已持仓")
        return
    
    print(f"\n{'='*70}")
    print(f"🔔 新增候选信号: {len(new_sigs)}只（已排除已持仓）")
    print(f"{'='*70}")
    print(f"  {'代码':<12s} {'名称':<8s} {'现价':>8s} {'回撤':>7s} {'跌天':>5s} {'日均':>7s} {'收敛':>6s} {'RSI':>5s} {'距MA20':>7s} {'距MA60':>7s} {'量比':>5s}")
    print("  " + "-" * 100)
    
    for s in new_sigs:
        print(f"  {s['ts_code']:<12s} {s['name']:<8s} {s['price']:>8.2f} {s['dd_pct']:>+6.1f}% {s['days']:>5d} {s['daily_drop']:>+6.2f}% {s['shrink']:>5.2f}x {s['rsi']:>5.0f} {s['dist_ma20']:>+6.1f}% {s['dist_ma60']:>+6.1f}% {s['vol_ratio']:>5.2f}")
    
    print(f"\n  💡 入场命令: python3 track.py --add 代码1,代码2,...")
    print(f"  📋 例如: python3 track.py --add {new_sigs[0]['ts_code']},{new_sigs[1]['ts_code']}")

# ==================== MAIN ====================
if __name__ == '__main__':
    init_db()
    
    if '--init' in sys.argv:
        print("✅ 初始化完成")
    
    elif '--report' in sys.argv:
        show_report()
    
    elif '--add' in sys.argv:
        idx = sys.argv.index('--add')
        codes = sys.argv[idx+1].split(',')
        for c in codes:
            add_position(c.strip())
        show_report()
    
    else:
        # 日常运行：扫描 + 通知 + 更新持仓
        bull, hs300, ma200 = is_bull_market()
        print(f"\nS008 每日扫描 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"{'🐂 牛市' if bull else '🐻 熊市'} | 沪深300={hs300:.0f} MA200={ma200:.0f}")
        
        if bull:
            t0 = time.time()
            signals = scan()
            print(f"扫描完成: {len(signals)}只候选 (耗时{time.time()-t0:.0f}s)")
            show_new_signals(signals)
        else:
            print("非牛市，跳过扫描")
        
        exited = update_positions()
        if exited > 0:
            print(f"\n➖ {exited}笔持仓满60天自动退出")
        
        show_report()
        
        # 记录日志
        conn = sqlite3.connect(DB_TRACK)
        existing = set(r[0] for r in conn.execute("SELECT ts_code FROM positions WHERE status='holding'"))
        new_count = sum(1 for s in signals if s['ts_code'] not in existing) if bull else 0
        conn.execute("INSERT INTO scan_log (scan_date, bull_status, new_signals, brief) VALUES (?,?,?,?)",
            (datetime.now().strftime('%Y-%m-%d'), '牛市' if bull else '熊市', new_count,
             f'新信号{new_count}' if bull else '熊市休眠'))
        conn.commit(); conn.close()
