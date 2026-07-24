#!/usr/bin/env python3
"""
每日策略监控 v3 — ST排除 + 亏损过滤 + 资金流向加分 + 信号去重
4条件: 250日跌>40% + K线收敛<75% + OBV背离 + MA5拐头
资金: 主力净流入趋势向上 → 信号强度+1级
持仓: 80交易日, 单笔5%
"""
import sqlite3, pandas as pd, numpy as np, requests, io, os
from scipy import stats

DB = os.path.expanduser('~/stock-data/stock_all.db')
TOKEN = '2b6b1b830a45468b9856e6500ce40a90'
BASE = 'https://ts.gyzcloud.top/api'
SIGNAL_GAP = 20  # 同股票信号间隔天数

def get_moneyflow(trade_date):
    """拉最新主力资金流向"""
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("""SELECT ts_code, CAST(net_mf_amount AS REAL) FROM moneyflow 
                WHERE trade_date=? AND CAST(net_mf_amount AS REAL) IS NOT NULL""", (trade_date,))
    mf = {r[0]: r[1] for r in cur.fetchall()}
    conn.close()
    return mf

def get_moneyflow_trend(ts_code, end_date):
    """60日资金流向趋势: slope>0=流入"""
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    start = (pd.to_datetime(end_date, format='%Y%m%d') - pd.DateOffset(days=120)).strftime('%Y%m%d')
    cur.execute("""SELECT trade_date, CAST(net_mf_amount AS REAL) FROM moneyflow 
                WHERE ts_code=? AND trade_date<=? AND trade_date>=?
                ORDER BY trade_date""", (ts_code, end_date, start))
    rows = cur.fetchall()
    conn.close()
    if len(rows) < 30:
        return False
    # 60日累计斜率
    vals = np.array([r[1] for r in rows])
    cumsum = np.cumsum(vals[-60:])
    if len(cumsum) < 30:
        return False
    slope, _ = np.polyfit(np.arange(len(cumsum)), cumsum, 1)[:2]
    return slope > 0

def build_blacklists():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS blacklist_st (ts_code TEXT PRIMARY KEY)")
    cur.execute("DELETE FROM blacklist_st")
    cur.execute("INSERT INTO blacklist_st SELECT ts_code FROM stock_list WHERE name LIKE '%ST%' OR name LIKE '%*ST%' OR name LIKE '%退%'")
    cur.execute("CREATE TABLE IF NOT EXISTS blacklist_loss (ts_code TEXT PRIMARY KEY)")
    cur.execute("DELETE FROM blacklist_loss")
    cur.execute("INSERT INTO blacklist_loss SELECT ts_code FROM income WHERE end_date='20251231' AND CAST(n_income_attr_p AS REAL) < 0")
    conn.commit()
    conn.close()

def scan():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    
    # 市场牛熊判断：上证距52周高点跌幅 >15% 为熊市(可开仓)
    r = requests.post(f'{BASE}/index_daily', json={
        'api_name':'index_daily','token':TOKEN,
        'params':{'ts_code':'000001.SH','start_date':'20250101','end_date':''},
        'fields':'trade_date,close'}, timeout=8)
    if r.status_code == 200:
        d = r.json()['data']
        idx_data = pd.DataFrame(d['items'], columns=d['fields']).astype({'close':float})
        idx_data = idx_data.sort_values('trade_date')
        high52 = idx_data['close'].rolling(250, min_periods=1).max().iloc[-1]
        idx_close = idx_data['close'].iloc[-1]
        dd_pct = (idx_close / high52 - 1) * 100
        is_bear = dd_pct < -15
        print(f'上证: {idx_close:.0f} | 52周高: {high52:.0f} | 跌幅: {dd_pct:+.1f}% | {"熊市-可开仓" if is_bear else "牛市-观望"}')
    else:
        is_bear = False
    
    cur.execute("SELECT MAX(trade_date) FROM daily")
    latest = cur.fetchone()[0]
    
    # Top300排除ST+亏损
    cur.execute("""
        SELECT d.ts_code FROM daily d
        WHERE d.trade_date >= strftime('%Y', date('now','-1 year'))||'0101'
        AND d.ts_code NOT IN (SELECT ts_code FROM blacklist_st)
        AND d.ts_code NOT IN (SELECT ts_code FROM blacklist_loss)
        GROUP BY d.ts_code ORDER BY AVG(CAST(d.vol AS REAL)) DESC LIMIT 300
    """)
    top = [r[0] for r in cur.fetchall()]
    cs = ','.join(f"'{c}'" for c in top)
    
    df_d = pd.read_sql(f"""SELECT ts_code,trade_date,CAST(open AS REAL)o,CAST(high AS REAL)h,
        CAST(low AS REAL)l,CAST(close AS REAL)c,CAST(vol AS REAL)v 
        FROM daily WHERE ts_code IN({cs}) AND trade_date>=strftime('%Y',date('now','-1 year'))||'0101' 
        ORDER BY ts_code,trade_date""", conn)
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
    
    # K线收敛
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
    
    # OBV背离
    obv_div_s = pd.Series(0, index=top, dtype=int)
    for c in top:
        cls = close_pv[c].dropna()
        if len(cls) < 250: continue
        obv=0; vals=[]; pc=None
        for d in cls.index:
            cl=cls.loc[d]; vo=vol_pv.loc[d,c] if d in vol_pv.index else np.nan
            if pd.isna(cl) or pd.isna(vo): continue
            if pc is not None:
                if cl>pc: obv+=vo
                elif cl<pc: obv-=vo
            pc=cl; vals.append(obv)
        if len(vals)<250: continue
        y=np.array(vals[-250:]); x=np.arange(250)
        valid=~(np.isnan(y))
        if valid.sum()<100: continue
        sl,_,_,_,_=stats.linregress(x[valid],y[valid])
        p=cls.values[-250:]; pn=p/p[0] if p[0]>0 else p
        ps,_,_,_,_=stats.linregress(np.arange(250),pn)
        if ps<0 and sl>0: obv_div_s[c]=1
    
    # 筛选
    deep = ret_250_s < -40
    conv = body_ratio_s < 0.75
    obv = obv_div_s == 1
    signal = deep & conv & obv
    signal = signal.dropna()
    hits = signal[signal == True].index.tolist()
    
    # 资金流入趋势
    mf_positive = {}
    for c in hits:
        mf_positive[c] = get_moneyflow_trend(c, latest)
    
    # 分级
    strong = [c for c in hits if mf_positive.get(c, False)]
    normal = [c for c in hits if not mf_positive.get(c, False)]
    
    print(f'  跌>40%: {deep.sum()}  收敛<75%: {conv.sum()}  OBV背离: {obv.sum()}  信号: {len(hits)}')
    
    # 信号去重
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS signal_history (ts_code TEXT, trade_date TEXT, strength TEXT, PRIMARY KEY(ts_code,trade_date))")
    recent_cutoff = (tdate - pd.DateOffset(days=SIGNAL_GAP)).strftime('%Y%m%d')
    cur.execute("SELECT ts_code FROM signal_history WHERE trade_date>=?", (recent_cutoff,))
    recent_codes = set(r[0] for r in cur.fetchall())
    
    all_hits = strong + normal
    all_hits = [c for c in all_hits if c not in recent_codes]
    
    # 牛熊过滤
    if not is_bear:
        print(f'\n  ⚠️ 当前为牛市，策略历史表现不佳（大众交通连续3次牛市信号全亏）。观望。')
        if all_hits:
            print(f'  发现{len(all_hits)}个技术信号但被市场过滤器拦截:')
            for c in all_hits:
                print(f'    {c}')
        conn.close()
        return False
    
    if all_hits:
        conn2 = sqlite3.connect(DB)
        cur2 = conn2.cursor()
        print(f'\n  {"代码":12s} {"名称":10s} {"跌幅":>8s} {"收敛比":>8s} {"资金流":>6s} {"强度":>6s}')
        print(f'  {"-"*55}')
        for c in all_hits:
            cur2.execute("SELECT name FROM stock_list WHERE ts_code=?", (c,))
            r=cur2.fetchone(); name=r[0] if r else c
            d=float(ret_250_s[c])
            br=body_ratio_s[c]
            mf='流入' if c in strong else '-'
            lvl='★★★' if c in strong else '★★☆'
            print(f'  {c:12s} {name:10s} {d:>+7.1f}% {br:>8.3f} {mf:>6s} {lvl:>6s}')
            # 记录信号
            cur.execute("INSERT OR IGNORE INTO signal_history VALUES(?,?,?)", 
                       (c, tdate.strftime('%Y%m%d'), 'strong' if c in strong else 'normal'))
        conn.commit()
        conn2.close()
        print(f'\n  ★★★ 资金流入确认 = 重仓信号 | ★★☆ OBV反转 = 标准信号')
        print(f'  策略: 一次性买入, 持80个交易日, 单笔5%仓位')
    else:
        print(f'\n  无新信号(近{SIGNAL_GAP}天已有同类信号)')
    
    conn.close()
    return len(all_hits) > 0

if __name__ == '__main__':
    import sys
    if '--build' in sys.argv:
        build_blacklists()
    scan()
