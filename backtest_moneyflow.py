#!/usr/bin/env python3
"""
策略回测 v3: OBV + 资金流向 双保险
信号分级:
  ★★★ OBV背离 + 资金流向为正 = 强信号
  ★★☆ 仅OBV背离 = 中信号  
  ★☆★ 仅资金流向为正 = 弱信号
"""
import sqlite3, pandas as pd, numpy as np
from scipy import stats
import os

DB = os.path.expanduser('~/stock-data/stock_all.db')

def get_top_stocks(n=300):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT ts_code FROM stock_list WHERE name NOT LIKE '%ST%' AND name NOT LIKE '%*ST%'")
    all_stocks = [r[0] for r in cur.fetchall()]
    cur.execute("""
        SELECT ts_code FROM daily WHERE trade_date>='20240101'
        GROUP BY ts_code ORDER BY AVG(CAST(vol AS REAL)) DESC LIMIT ?
    """, (n,))
    top = [r[0] for r in cur.fetchall() if r[0] in all_stocks]
    conn.close()
    return top

def load_data(stocks):
    conn = sqlite3.connect(DB)
    cs = ','.join(f"'{c}'" for c in stocks)
    daily = pd.read_sql(f"""
        SELECT ts_code,trade_date,CAST(open AS REAL)o,CAST(high AS REAL)h,
               CAST(low AS REAL)l,CAST(close AS REAL)c,CAST(vol AS REAL)v
        FROM daily WHERE ts_code IN({cs}) AND trade_date>='20230101'
        ORDER BY ts_code,trade_date""", conn)
    
    mf = pd.read_sql(f"""
        SELECT ts_code,trade_date,CAST(net_mf_amount AS REAL) net_mf
        FROM moneyflow WHERE ts_code IN({cs}) AND trade_date>='20230101'
        ORDER BY ts_code,trade_date""", conn)
    conn.close()
    return daily, mf

def run_backtest():
    stocks = get_top_stocks(200)
    print(f'股票池: {len(stocks)}只')
    
    daily, mf = load_data(stocks)
    daily['trade_date'] = pd.to_datetime(daily['trade_date'], format='%Y%m%d')
    mf['trade_date'] = pd.to_datetime(mf['trade_date'], format='%Y%m%d')
    
    close_pv = daily.pivot(index='trade_date', columns='ts_code', values='c').sort_index()
    vol_pv = daily.pivot(index='trade_date', columns='ts_code', values='v').sort_index()
    mf_pv = mf.pivot(index='trade_date', columns='ts_code', values='net_mf').sort_index()
    
    # 检查信号
    signal_data = []  # {ts_code, date, type: 'obv_only'|'mf_only'|'both'}
    
    all_dates = list(close_pv.index)
    scan_dates = [d for d in all_dates if d >= pd.Timestamp('2024-07-01')]
    
    for idx_t in range(len(all_dates)):
        tdate = all_dates[idx_t]
        if tdate not in scan_dates:
            continue
        
        idx_250 = idx_t - 250
        if idx_250 < 0:
            continue
        prev_t = all_dates[idx_250]
        
        ret_250 = (close_pv.loc[tdate] / close_pv.loc[prev_t] - 1) * 100
        deep = ret_250[ret_250 < -40]
        if len(deep) == 0:
            continue
        
        # 收敛检查
        conv_stocks = []
        for c in deep.index:
            cd = daily[daily['ts_code'] == c].sort_values('trade_date').set_index('trade_date')
            cd['body'] = abs(cd['c'] - cd['o']) / cd['c']
            cd['b60'] = cd['body'].rolling(60).mean()
            cd['bp60'] = cd['body'].shift(60).rolling(60).mean()
            cd['ratio'] = cd['b60'] / (cd['bp60'] + 1e-10)
            if tdate in cd.index:
                v = cd.loc[tdate, 'ratio']
                if not pd.isna(v) and v < 0.75:
                    conv_stocks.append(c)
        if not conv_stocks:
            continue
        
        for c in conv_stocks:
            # OBV背离
            cls = close_pv[c].dropna()
            if tdate not in cls.index or len(cls) < 250:
                continue
            vals = []; obv = 0; pc = None
            for d in cls.index:
                cl = cls.loc[d]; vo = vol_pv.loc[d, c] if d in vol_pv.index else np.nan
                if pd.isna(cl) or pd.isna(vo): continue
                if pc is not None:
                    if cl > pc: obv += vo
                    elif cl < pc: obv -= vo
                pc = cl; vals.append(obv)
            cls_arr = cls.values[-250:]
            obv_arr = np.array(vals[-250:])
            valid = ~(np.isnan(cls_arr) | np.isnan(obv_arr))
            if valid.sum() < 100: continue
            ps, _ = np.polyfit(np.arange(250)[valid], cls_arr[valid], 1)[:2]
            os, _ = np.polyfit(np.arange(250)[valid], obv_arr[valid], 1)[:2]
            obv_signal = (ps < 0 and os > 0)
            
            # 资金流向信号: 60日累计主力净流入斜率 > 0
            mf_signal = False
            if c in mf_pv.columns and tdate in mf_pv.index:
                mf_series = mf_pv[c].dropna()
                if len(mf_series) > 60:
                    mf_cumsum = mf_series.rolling(60).sum().dropna()
                    if len(mf_cumsum) > 30:
                        x = np.arange(len(mf_cumsum))
                        mf_slope, _ = np.polyfit(x, mf_cumsum.values, 1)[:2]
                        mf_signal = (mf_slope > 0)
            
            if obv_signal or mf_signal:
                tp = 'both' if (obv_signal and mf_signal) else ('obv_only' if obv_signal else 'mf_only')
                signal_data.append({'ts_code': c, 'date': tdate, 'type': tp, 'ret_250': float(ret_250[c])})
    
    print(f'总信号(去重前): {len(signal_data)}条')
    
    # 去重: 同一股票两次信号至少间隔20个交易日
    signal_data.sort(key=lambda x: (x['ts_code'], x['date']))
    deduped = []
    last_seen = {}
    for s in signal_data:
        c = s['ts_code']
        if c in last_seen:
            days_gap = (s['date'] - last_seen[c]).days
            if days_gap < 20:
                continue
        deduped.append(s)
        last_seen[c] = s['date']
    signal_data = deduped
    print(f'总信号(去重后): {len(signal_data)}条')
    
    # 统计各类信号
    from collections import Counter
    types = Counter(s['type'] for s in signal_data)
    for t in ['both', 'obv_only', 'mf_only']:
        print(f'  {t}: {types.get(t, 0)}条')
    
    # 持仓收益分析
    results = []
    for s in signal_data:
        c, d, tp = s['ts_code'], s['date'], s['type']
        cls_series = close_pv[c].dropna()
        dates = list(cls_series.index)
        try:
            idx = dates.index(d)
        except ValueError:
            continue
        
        for hold_days in [20, 40, 60, 80, 120, 160, 200, 250]:
            fut_idx = idx + hold_days
            if fut_idx >= len(dates):
                continue
            entry = cls_series.iloc[idx]
            exit_p = cls_series.iloc[fut_idx]
            ret = (exit_p / entry - 1) * 100
            results.append({'ts_code': c, 'date': d, 'type': tp, 'hold': hold_days, 'ret': ret, 'entry': entry,
                          'exit': exit_p, 'ret_250': s['ret_250']})
    
    df_r = pd.DataFrame(results)
    
    # 按信号类型分组统计
    print(f'\n{"信号类型":12s} {"持有":>6s} {"交易数":>6s} {"胜率":>8s} {"均收益率":>10s} {"中位收益率":>10s} {"最大单笔":>10s}')
    print('-' * 70)
    
    summary = []
    for tp_name, tp_label in [('both', 'OBV+资金'), ('obv_only', '仅OBV'), ('mf_only', '仅资金'), ('all', '全部')]:
        if tp_name == 'all':
            subset = df_r
        else:
            subset = df_r[df_r['type'] == tp_name]
        for hold in [20, 40, 60, 80, 120, 160, 200, 250]:
            sub = subset[subset['hold'] == hold]
            if len(sub) < 3:
                continue
            win = (sub['ret'] > 0).mean()
            avg = sub['ret'].mean()
            med = sub['ret'].median()
            mx = sub['ret'].max()
            print(f'{tp_label:12s} {hold:>5d}天 {len(sub):>5d}笔 {win:>7.1%} {avg:>+9.1f}% {med:>+9.1f}% {mx:>+9.1f}%')
            summary.append({'type': tp_label, 'hold': hold, 'n': len(sub), 'win_rate': win, 'avg_ret': avg, 'entry': sub['entry'].mean() if len(sub) > 0 else 0})
    
    return df_r, summary

if __name__ == '__main__':
    df_r, summary = run_backtest()
    # 保存
    df_r.to_csv(os.path.expanduser('~/stock-tools/signal_returns_v3.csv'), index=False)
    print(f'\n结果已保存 ~/stock-tools/signal_returns_v3.csv')
