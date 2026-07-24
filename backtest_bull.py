#!/usr/bin/env python3
"""牛市趋势策略回测"""
import sqlite3, pandas as pd, numpy as np

DB='/Users/ziruzhu/stock-data/stock_all.db'
conn=sqlite3.connect(DB)
cur=conn.cursor()

cur.execute('SELECT ts_code FROM blacklist_st UNION SELECT ts_code FROM blacklist_loss')
blacklist=set(r[0] for r in cur.fetchall())

df=pd.read_sql("""SELECT ts_code,trade_date,CAST(open AS REAL)o,
    CAST(high AS REAL)h,CAST(low AS REAL)l,CAST(close AS REAL)c,
    CAST(vol AS REAL)v FROM daily WHERE trade_date>='20230101'
    ORDER BY ts_code,trade_date""",conn)
conn.close()

df['trade_date']=pd.to_datetime(df['trade_date'],format='%Y%m%d')
close_pv=df.pivot(index='trade_date',columns='ts_code',values='c').sort_index()
vol_pv=df.pivot(index='trade_date',columns='ts_code',values='v').sort_index()

dates=list(close_pv.index)
signals=[]

# 从2024年中开始扫描
for i in range(120,len(dates)):
    tdate=dates[i]
    
    for c in close_pv.columns:
        if c in blacklist: continue
        cls=close_pv[c].dropna()
        if tdate not in cls.index: continue
        
        # MA60判断
        ma60=cls.rolling(60).mean()
        if tdate not in ma60.index or pd.isna(ma60.loc[tdate]): continue
        if cls.loc[tdate] < ma60.loc[tdate]: continue
        
        # 斜率
        ma60_vals=ma60.dropna()
        if len(ma60_vals)<20: continue
        idx_in_ma=list(ma60_vals.index).index(tdate) if tdate in ma60_vals.index else -1
        if idx_in_ma<20: continue
        slope,_=np.polyfit(np.arange(20),ma60_vals.values[idx_in_ma-20:idx_in_ma],1)[:2]
        if slope<=0: continue
        
        # 10日涨幅
        idx_cls=list(cls.index).index(tdate)
        if idx_cls<11: continue
        ret10=(cls.iloc[idx_cls]/cls.iloc[idx_cls-11]-1)*100
        if ret10<10: continue
        
        # 放量
        if c in vol_pv.columns:
            v=vol_pv[c].dropna()
            vidx=list(v.index).index(tdate) if tdate in v.index else -1
            if vidx<20: continue
            v5=v.values[vidx-5:vidx].mean()
            v20=v.values[vidx-20:vidx].mean()
            if v20==0 or v5/v20<1.3: continue
        else:
            continue
        
        signals.append({'ts_code':c,'date':tdate,'ret10':ret10,'slope':slope})

# 去重(20天间隔)
signals.sort(key=lambda x:(x['ts_code'],x['date']))
deduped=[]
last={}
for s in signals:
    if s['ts_code'] in last:
        if (s['date']-last[s['ts_code']]).days<20: continue
    deduped.append(s)
    last[s['ts_code']]=s['date']

print(f'信号: {len(signals)}→去重{len(deduped)}条')

# 计算未来收益
results=[]
for s in deduped:
    c=s['ts_code']; d=s['date']
    cls=close_pv[c].dropna()
    try:
        idx=list(cls.index).index(d)
    except: continue
    
    for hold in [10,20,30,40,60]:
        if idx+hold>=len(cls): continue
        ret=(cls.iloc[idx+hold]/cls.iloc[idx]-1)*100
        results.append({'ts_code':c,'date':d,'hold':hold,'ret':ret,'ret10':s['ret10']})

df_r=pd.DataFrame(results)

# 统计
print(f'\n{"持有":>6s} {"笔数":>6s} {"胜率":>8s} {"均收益":>10s} {"中位":>10s}')
print('-'*50)
for hold in [10,20,30,40,60]:
    sub=df_r[df_r['hold']==hold]
    if len(sub)<3: continue
    w=(sub['ret']>0).mean()
    avg=sub['ret'].mean(); med=sub['ret'].median()
    print(f'{hold:>5d}天 {len(sub):>5d}笔 {w:>7.0%} {avg:>+9.1f}% {med:>+9.1f}%')

df_r.to_csv('/Users/ziruzhu/stock-tools/bull_returns.csv',index=False)
print('\n已保存 bull_returns.csv')
