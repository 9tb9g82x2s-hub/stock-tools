#!/usr/bin/env python3
"""
每日策略监控 v2 — ST排除 + 亏损股过滤
条件: 250日跌>40% + K线收敛<75% + OBV背离 + MA5拐头(加分)
纯本地DB, 不用外网API
"""
import sqlite3, pandas as pd, numpy as np
from scipy import stats, integrate
import os

DB = os.path.expanduser('~/stock-data/stock_all.db')

def build_blacklists():
    """从本地数据库构建ST和亏损股黑名单"""
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    
    # 1. ST黑名单 — 从stock_list名称筛选
    cur.execute("CREATE TABLE IF NOT EXISTS blacklist_st (ts_code TEXT PRIMARY KEY)")
    cur.execute("DELETE FROM blacklist_st")
    cur.execute("""
        INSERT INTO blacklist_st 
        SELECT ts_code FROM stock_list 
        WHERE name LIKE '%ST%' OR name LIKE '%*ST%' OR name LIKE '%退%'
    """)
    n_st = cur.rowcount
    print(f"  ST黑名单: {n_st}只")
    
    # 2. 亏损股 — 从income表取最近年报
    cur.execute("CREATE TABLE IF NOT EXISTS blacklist_loss (ts_code TEXT PRIMARY KEY)")
    cur.execute("DELETE FROM blacklist_loss")
    cur.execute("""
        INSERT INTO blacklist_loss
        SELECT ts_code FROM income 
        WHERE end_date='20251231' AND CAST(n_income_attr_p AS REAL) < 0
    """)
    n_loss = cur.rowcount
    print(f"  亏损黑名单: {n_loss}只")
    
    conn.commit()
    conn.close()

def scan():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    
    cur.execute("SELECT MAX(trade_date) FROM daily")
    latest = cur.fetchone()[0]
    
    # 查黑名单是否存在
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='blacklist_st'")
    has_bl = cur.fetchone() is not None
    
    # 高流动性Top300 (排除ST和亏损)
    if has_bl:
        cur.execute("""
            SELECT d.ts_code FROM daily d
            WHERE d.trade_date >= '20260101'
            AND d.ts_code NOT IN (SELECT ts_code FROM blacklist_st)
            AND d.ts_code NOT IN (SELECT ts_code FROM blacklist_loss)
            GROUP BY d.ts_code ORDER BY AVG(CAST(d.vol AS REAL)) DESC LIMIT 300
        """)
    else:
        cur.execute("""
            SELECT ts_code FROM daily WHERE trade_date>='20260101'
            AND ts_code NOT IN (SELECT ts_code FROM stock_list WHERE name LIKE '%ST%' OR name LIKE '%*ST%')
            GROUP BY ts_code ORDER BY AVG(CAST(vol AS REAL)) DESC LIMIT 300
        """)
    top = [r[0] for r in cur.fetchall()]
    
    cs = ','.join(f"'{c}'" for c in top)
    
    df_d = pd.read_sql(f"SELECT ts_code,trade_date,CAST(open AS REAL)o,CAST(high AS REAL)h,CAST(low AS REAL)l,CAST(close AS REAL)c,CAST(vol AS REAL)v FROM daily WHERE ts_code IN({cs})AND trade_date>=strftime('%Y',date('now','-1 year'))||'0101' ORDER BY ts_code,trade_date", conn)
    conn.close()
    
    df_d['trade_date'] = pd.to_datetime(df_d['trade_date'], format='%Y%m%d')
    close_pv = df_d.pivot(index='trade_date', columns='ts_code', values='c').sort_index()
    vol_pv = df_d.pivot(index='trade_date', columns='ts_code', values='v').sort_index()
    
    tdate = close_pv.index[-1]
    tdates = list(close_pv.index)
    idx_t = len(tdates) - 1
    prev_t = tdates[idx_t - 250] if idx_t >= 250 else tdates[0]
    
    print(f'= 扫描: {tdate.strftime("%Y-%m-%d")} | 股票池: {len(top)}只 =\n')
    
    ret_250_s = (close_pv.loc[tdate] / close_pv.loc[prev_t] - 1) * 100
    ma5 = close_pv.rolling(5).mean()
    ma5_turn = ma5.loc[tdate] > ma5.shift(1).loc[tdate] if tdate in ma5.shift(1).index else pd.Series(False, index=ret_250_s.index)
    
    body_ratios = {}
    for c in top:
        cd = df_d[df_d['ts_code'] == c].sort_values('trade_date').set_index('trade_date')
        cd['body'] = abs(cd['c'] - cd['o']) / cd['c']
        cd['b60'] = cd['body'].rolling(60).mean()
        cd['bp60'] = cd['body'].shift(60).rolling(60).mean()
        cd['ratio'] = cd['b60'] / (cd['bp60'] + 1e-10)
        if tdate in cd.index:
            v = cd.loc[tdate, 'ratio']
            body_ratios[c] = float(v) if not pd.isna(v) else np.nan
    body_ratio_s = pd.Series(body_ratios, index=top)
    
    obv_div_s = pd.Series(0, index=top, dtype=int)
    for c in top:
        cls = close_pv[c].dropna()
        if len(cls) < 250: continue
        obv = 0; vals = []; pc = None
        for d in cls.index:
            cl = cls.loc[d]; vo = vol_pv.loc[d, c] if d in vol_pv.index else np.nan
            if pd.isna(cl) or pd.isna(vo): continue
            if pc is not None:
                if cl > pc: obv += vo
                elif cl < pc: obv -= vo
            pc = cl; vals.append(obv)
        if len(vals) < 250: continue
        y = np.array(vals[-250:]); base = abs(y[0]) if y[0] != 0 else 1
        y_n = y / base; x = np.arange(250); valid = ~np.isnan(y_n)
        if valid.sum() < 100: continue
        sl, _, _, _, _ = stats.linregress(x[valid], y_n[valid])
        p = cls.values[-250:]; pn = p / p[0] if p[0] > 0 else p
        ps, _, _, _, _ = stats.linregress(np.arange(250), pn)
        if ps < 0 and sl > 0: obv_div_s[c] = 1
    
    deep = ret_250_s < -40
    conv = body_ratio_s < 0.75
    obv = obv_div_s == 1
    signal = deep & conv & obv
    signal = signal.dropna()
    hits = signal[signal == True].index.tolist()
    
    print(f'  跌>40%: {deep.sum()}  收敛<75%: {conv.sum()}  OBV背离: {obv.sum()}  信号: {len(hits)}')
    
    if hits:
        conn2 = sqlite3.connect(DB)
        cur2 = conn2.cursor()
        print(f'\n  {"代码":12s} {"名称":10s} {"跌幅":>8s} {"收敛比":>8s} {"MA5":>6s}')
        print(f'  {"-"*48}')
        for c in hits:
            cur2.execute("SELECT name FROM stock_list WHERE ts_code=?", (c,))
            r = cur2.fetchone(); name = r[0] if r else c
            d = float(ret_250_s[c])
            br = body_ratio_s[c]
            mt = '拐头' if c in ma5_turn.index and ma5_turn[c] else ''
            print(f'  {c:12s} {name:10s} {d:>+7.1f}% {br:>8.3f} {mt:>6s}')
        conn2.close()
        print(f'\n  策略: 一次性买入, 持80个交易日, 单笔≤5%仓位')
    else:
        print(f'\n  无信号。')
    
    return len(hits) > 0

if __name__ == '__main__':
    import sys
    if '--build' in sys.argv:
        build_blacklists()
    scan()
