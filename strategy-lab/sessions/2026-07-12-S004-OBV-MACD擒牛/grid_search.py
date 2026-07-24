#!/usr/bin/env python3
"""
S004 参数网格搜索：OBV夹角阈值 × OBV均线周期
只调最核心的两个参数，其他保持不变
"""
import sqlite3, pandas as pd, numpy as np, os, time, json
from itertools import product

DB = os.path.expanduser('~/stock-data/stock_all.db')
OUT = os.path.dirname(os.path.abspath(__file__))
TOP_STOCKS = 500
START_DATE = '2022-01-01'

GRID = {
    'obv_angle': [0.20, 0.35, 0.50],
    'obv_ma':     [15, 20, 25, 30],
}

def get_stock_pool():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT ts_code FROM stock_list WHERE name NOT LIKE '%ST%' AND name NOT LIKE '%*ST%' AND name NOT LIKE '%退%'")
    clean = set(r[0] for r in cur.fetchall())
    cur.execute("""
        SELECT ts_code FROM daily WHERE trade_date>='20240101' AND trade_date<'20260101'
        GROUP BY ts_code ORDER BY AVG(CAST(vol AS REAL)*CAST(close AS REAL)) DESC LIMIT ?
    """, (TOP_STOCKS*2,))
    pool = [r[0] for r in cur.fetchall() if r[0] in clean][:TOP_STOCKS]
    conn.close()
    return pool

def compute_indicators(close, vol, obv_ma_period):
    """预计算所有需要的指标"""
    if len(close) < 260: return None
    
    # OBV
    diff = np.diff(close, prepend=close[0])
    sign = np.where(diff>0, 1, np.where(diff<0, -1, 0))
    obv = np.cumsum(sign * vol)
    maobv = pd.Series(obv).rolling(obv_ma_period).mean().values
    
    # MACD
    ema12 = pd.Series(close).ewm(span=12, adjust=False).mean().values
    ema26 = pd.Series(close).ewm(span=26, adjust=False).mean().values
    dif = ema12 - ema26
    dea = pd.Series(dif).ewm(span=9, adjust=False).mean().values
    
    # MA
    ma5 = pd.Series(close).rolling(5).mean().values
    ma10 = pd.Series(close).rolling(10).mean().values
    ma20 = pd.Series(close).rolling(20).mean().values
    
    # 预计算 rolling 统计（用于加速条件判断）
    # 近20天 OBV>MAOBV 天数
    obv_above_ma = (obv > maobv).astype(float)
    obv_above_20d = pd.Series(obv_above_ma).rolling(20, min_periods=1).sum().values
    
    # 60天 OBV 标准差（每个位置）
    obv_std_60 = pd.Series(obv).rolling(60, min_periods=1).std().values
    
    # 近30天 DIF>0 AND DEA>0 天数
    macd_above = ((dif > 0) & (dea > 0)).astype(float)
    macd_above_30d = pd.Series(macd_above).rolling(30, min_periods=1).sum().values
    
    # 前30天振幅（用于平台判断）
    h = None  # 需要从原始数据获取
    
    return {
        'close': close, 'vol': vol,
        'obv': obv, 'maobv': maobv,
        'dif': dif, 'dea': dea,
        'ma5': ma5, 'ma10': ma10, 'ma20': ma20,
        'obv_above_20d': obv_above_20d,
        'obv_std_60': obv_std_60,
        'macd_above_30d': macd_above_30d,
    }

def detect_signals_grid(indicators, params, high, low):
    """对给定参数检测信号，返回日期列表"""
    obv = indicators['obv']
    maobv = indicators['maobv']
    dif = indicators['dif']
    dea = indicators['dea']
    ma5 = indicators['ma5']
    ma10 = indicators['ma10']
    ma20 = indicators['ma20']
    c = indicators['close']
    
    angle_th = params['obv_angle']
    ratio_th = 0.80  # 条件1a固定80%
    macd_th = 0.67   # 条件2固定67%
    accel_days = 5
    
    n = len(c)
    if n < 260: return []
    
    signals = []
    for i in range(259, n):
        # 条件1a
        valid_20 = min(20, i+1)
        above = indicators['obv_above_20d'][i]
        if valid_20 < 16: continue
        cond_1a = above >= valid_20 * ratio_th
        
        # 条件1b
        obv_std = indicators['obv_std_60'][i]
        if np.isnan(obv_std) or obv_std < 1:
            if not cond_1a: continue
            cond_1b = False
        else:
            obv_5 = obv[i-accel_days+1:i+1]
            maobv_5 = maobv[i-accel_days+1:i+1]
            if np.any(np.isnan(obv_5)) or np.any(np.isnan(maobv_5)):
                cond_1b = False
            else:
                diff_5 = obv_5 - maobv_5
                x = np.arange(len(diff_5))
                slope, _ = np.polyfit(x, diff_5, 1)[:2]
                cond_1b = slope / obv_std > angle_th
        
        # 条件2
        macd_valid = min(30, i+1)
        macd_above = indicators['macd_above_30d'][i]
        if macd_valid < 20: continue
        cond_2 = macd_above >= macd_valid * macd_th
        
        # 条件3: MA5金叉
        if i < 2: continue
        if np.isnan(ma5[i]) or np.isnan(ma10[i]) or np.isnan(ma20[i]): continue
        if np.isnan(ma5[i-1]) or np.isnan(ma10[i-1]) or np.isnan(ma20[i-1]): continue
        today = ma5[i] > ma10[i] and ma5[i] > ma20[i]
        yesterday = ma5[i-1] <= ma10[i-1] or ma5[i-1] <= ma20[i-1]
        cond_3 = today and yesterday
        
        # 条件4: 平台整理
        plat_start = i - 40
        plat_end = i - 10
        cond_4 = False
        if plat_start >= 0:
            ph = np.max(high[plat_start:plat_end+1]) if high is not None else c[plat_start:plat_end+1].max()*1.05
            pl = np.min(low[plat_start:plat_end+1]) if low is not None else c[plat_start:plat_end+1].min()*0.95
            pm = np.mean(c[plat_start:plat_end+1])
            if pm > 0:
                cond_4 = (ph - pl) / pm < 0.20
        
        if cond_1a and cond_1b and cond_2 and cond_3:
            signals.append((i, 'strong' if cond_4 else 'solid'))
        elif cond_1a and cond_2 and cond_3:
            signals.append((i, 'medium'))
        elif cond_2 and cond_3:
            signals.append((i, 'weak'))
    
    return signals


def run_grid_search():
    stocks = get_stock_pool()
    print(f'股票池: {len(stocks)}只')
    
    combos = list(product(GRID['obv_angle'], GRID['obv_ma']))
    print(f'参数组合: {len(combos)}组')
    print(f'  OBV夹角: {GRID["obv_angle"]}')
    print(f'  OBV均线: {GRID["obv_ma"]}')
    
    conn = sqlite3.connect(DB)
    
    # 预加载所有股票数据
    print('\n[1/3] 预加载数据...')
    cs = ','.join(f"'{c}'" for c in stocks)
    daily_all = pd.read_sql(f"""
        SELECT ts_code, trade_date, 
               CAST(open AS REAL) as o, CAST(high AS REAL) as h,
               CAST(low AS REAL) as l, CAST(close AS REAL) as c, CAST(vol AS REAL) as v
        FROM daily WHERE ts_code IN ({cs}) AND trade_date>='20210101'
        ORDER BY ts_code, trade_date
    """, conn)
    daily_all['trade_date'] = pd.to_datetime(daily_all['trade_date'], format='%Y%m%d')
    conn.close()
    
    # 构建 close pivot 用于计算收益
    close_pv = daily_all.pivot(index='trade_date', columns='ts_code', values='c').sort_index()
    
    # 对12组参数，公共部分（MACD、MA等）用obv_ma=20的指标，只改变OBV部分的计算
    # 但OBV MA周期不同会导致MAOBV不同，需要分别算
    # 高效方案：按OBV MA分组，每组重新算一次OBV相关指标
    
    print('[2/3] 逐组扫描...')
    all_results = []
    
    for obv_ma in GRID['obv_ma']:
        t0 = time.time()
        
        # 对这一组MA周期，重新计算所有股票的OBV和MAOBV
        stock_indicators = {}
        for ts_code in stocks:
            sdata = daily_all[daily_all['ts_code']==ts_code].sort_values('trade_date')
            if len(sdata) < 260: continue
            c_arr = sdata['c'].values
            v_arr = sdata['v'].values
            h_arr = sdata['h'].values
            l_arr = sdata['l'].values
            
            ind = compute_indicators(c_arr, v_arr, obv_ma)
            if ind is None: continue
            ind['dates'] = list(sdata['trade_date'])
            ind['high'] = h_arr
            ind['low'] = l_arr
            stock_indicators[ts_code] = ind
        
        for obv_angle in GRID['obv_angle']:
            params = {'obv_angle': obv_angle, 'obv_ma': obv_ma}
            key = f'angle{obv_angle}_ma{obv_ma}'
            
            # 检测信号
            all_signals = []  # (ts_code, date, strength, has_platform)
            for ts_code, ind in stock_indicators.items():
                sigs = detect_signals_grid(ind, params, ind.get('high'), ind.get('low'))
                for idx, strength in sigs:
                    d = ind['dates'][idx]
                    if d >= pd.Timestamp(START_DATE):
                        has_plat = (strength == 'strong')
                        all_signals.append((ts_code, d, strength, has_plat))
            
            # 去重
            all_signals.sort(key=lambda x: (x[0], x[1]))
            deduped = []
            last_seen = {}
            for ts_code, d, strength, has_plat in all_signals:
                if ts_code in last_seen and (d - last_seen[ts_code]).days < 20:
                    continue
                deduped.append((ts_code, d, strength, has_plat))
                last_seen[ts_code] = d
            
            # 计算收益（稳健版160天 + 激进版20天）
            results = []
            for ts_code, sig_date, strength, has_plat in deduped:
                if ts_code not in close_pv.columns: continue
                cls = close_pv[ts_code].dropna()
                if sig_date not in cls.index: continue
                dl = list(cls.index)
                try: idx = dl.index(sig_date)
                except: continue
                entry = float(cls.iloc[idx])
                
                # 稳健版：实信号160天
                if strength in ('solid', 'strong'):
                    fut = idx + 160
                    if fut < len(dl):
                        ret = (float(cls.iloc[fut]) / entry - 1) * 100
                        results.append({'params': key, 'angle': obv_angle, 'ma': obv_ma,
                                       'version': 'steady', 'strength': strength,
                                       'hold': 160, 'return': ret, 'has_plat': has_plat,
                                       'date': sig_date.strftime('%Y-%m-%d')})
                
                # 激进版：强信号20天
                if strength == 'strong':
                    fut = idx + 20
                    if fut < len(dl):
                        ret = (float(cls.iloc[fut]) / entry - 1) * 100
                        results.append({'params': key, 'angle': obv_angle, 'ma': obv_ma,
                                       'version': 'aggressive', 'strength': strength,
                                       'hold': 20, 'return': ret, 'has_plat': has_plat,
                                       'date': sig_date.strftime('%Y-%m-%d')})
            
            all_results.extend(results)
            solid_strong = sum(1 for x in deduped if x[2] in ('solid', 'strong'))
            print(f'  {key}: 信号{len(deduped)}(solid/strong: {solid_strong})  耗时{time.time()-t0:.0f}s')
        
        print(f'  MA={obv_ma} 组完成, 总耗时 {time.time()-t0:.0f}s')
    
    # === 汇总 ===
    df_grid = pd.DataFrame(all_results)
    df_grid.to_csv(os.path.join(OUT, 'grid_results.csv'), index=False)
    
    print(f'\n[3/3] 汇总结果 ({len(df_grid)}条)')
    
    # 稳健版汇总
    sep80 = '='*80
    sep65 = '-'*65
    print(f'\n{sep80}')
    print('稳健版（solid/strong信号 · 160天持有）')
    print(sep80)
    steady = df_grid[df_grid['version']=='steady']
    print(f'  {"参数":<20s} {"信号":>5s} {"胜率":>6s} {"均收益":>10s} {"中位":>10s} {"赚>20%":>8s}')
    print(f'  {sep65}')
    for key in sorted(steady['params'].unique()):
        sub = steady[steady['params']==key]
        if len(sub) < 5: continue
        win = (sub['return']>0).mean()
        ret_col = sub['return']
        print(f'  {key:<20s} {len(sub):>4}笔 {win:>5.0%} {ret_col.mean():>+9.2f}% {ret_col.median():>+9.2f}% {(ret_col>20).mean():>7.0%}')
    
    # 激进版汇总
    print('\n' + sep80)
    print('激进版（strong信号 · 20天持有）')
    print(sep80)
    aggro = df_grid[df_grid['version']=='aggressive']
    print(f'  {"参数":<20s} {"信号":>5s} {"胜率":>6s} {"均收益":>10s} {"中位":>10s} {"赚>10%":>8s}')
    print(f'  {sep65}')
    for key in sorted(aggro['params'].unique()):
        sub = aggro[aggro['params']==key]
        if len(sub) < 3: continue
        win = (sub['return']>0).mean()
        ret_col = sub['return']
        print(f'  {key:<20s} {len(sub):>4}笔 {win:>5.0%} {ret_col.mean():>+9.2f}% {ret_col.median():>+9.2f}% {(ret_col>10).mean():>7.0%}')
    
    # 找最优
    print('\n' + sep80)
    print('综合排名（稳健版中位收益）')
    print(sep80)
    ranking = []
    for key in steady['params'].unique():
        sub = steady[steady['params']==key]
        if len(sub) < 5: continue
        ranking.append((key, len(sub), sub['return'].median(), sub['return'].mean(), (sub['return']>0).mean()))
    ranking.sort(key=lambda x: x[2], reverse=True)
    for i, (k, n, med, avg, win) in enumerate(ranking[:5]):
        print(f'  #{i+1} {k}: {n}笔 中位{med:+.2f}% 均{avg:+.2f}% 胜率{win:.0%}')
    
    print(f'\n结果保存: grid_results.csv')
    return df_grid

if __name__ == '__main__':
    run_grid_search()
