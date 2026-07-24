#!/usr/bin/env python3
"""牛市趋势回测 — 轻量版: 按窗口聚合，不加载全量"""
import sqlite3, numpy as np, pandas as pd

DB='/Users/ziruzhu/stock-data/stock_all.db'
conn=sqlite3.connect(DB)

# 采样: 每月最后交易日扫描，取Top300流动性
dates=pd.date_range('2024-06-01','2026-07-01',freq='ME')
results=[]

for dt in dates:
    ds=dt.strftime('%Y%m%d')
    
    # 获取当天存量股票
    cur=conn.cursor()
    cur.execute("""SELECT ts_code,CAST(close AS REAL)c,CAST(vol AS REAL)v
        FROM daily WHERE trade_date=?""",(ds,))
    rows=cur.fetchall()
    if len(rows)<100: continue
    
    stocks=pd.DataFrame(rows,columns=['ts_code','c','v'])
    
    # 牛市信号条件
    candidates=[]
    for _,r in stocks.iterrows():
        c=r['ts_code']; close=r['c']
        # 拉60日历史
        hist=pd.read_sql(f"""SELECT trade_date,CAST(close AS REAL)c,CAST(vol AS REAL)v
            FROM daily WHERE ts_code='{c}' AND trade_date<='{ds}'
            ORDER BY trade_date DESC LIMIT 120""",conn)
        if len(hist)<60: continue
        hist=hist.sort_values('trade_date')
        
        # 强动量: 20日涨幅 >20%
        if len(hist)<21: continue
        ret20=(close/hist['c'].iloc[-21]-1)*100
        if ret20<20: continue
        
        # 连续5天站上MA60
        above_ma=hist['c'].iloc[-5:] > hist['c'].rolling(60).mean().iloc[-5:]
        if not above_ma.all(): continue
        
        # MA60斜率
        ma60_vals=hist['c'].rolling(60).mean().dropna()
        if len(ma60_vals)<20: continue
        slope,_=np.polyfit(np.arange(20),ma60_vals.values[-20:],1)[:2]
        if slope<=0: continue
        
        # 持续放量: 20日均量 > 60日均量
        v20=hist['v'].iloc[-20:].mean()
        v60=hist['v'].iloc[-60:].mean() if len(hist)>=60 else v20
        if v20<v60*1.2: continue
        
        # 计算未来收益
        fut=pd.read_sql(f"""SELECT trade_date,CAST(close AS REAL)c
            FROM daily WHERE ts_code='{c}' AND trade_date>'{ds}'
            ORDER BY trade_date LIMIT 60""",conn)
        if len(fut)==0: continue
        
        entry=close
        rets={'ts_code':c,'date':ds}
        for h in [10,20,30,40,60]:
            if h<len(fut):
                rets[f'ret_{h}']=(fut['c'].iloc[h-1]/entry-1)*100
            else:
                rets[f'ret_{h}']=np.nan
        candidates.append(rets)
    
    results.extend(candidates)
    cur.close()

conn.close()

df=pd.DataFrame(results)
if len(df)==0:
    print('无信号')
else:
    print(f'总信号: {len(df)}条')
    for h in [10,20,30,40,60]:
        col=f'ret_{h}'
        valid=df[col].dropna()
        if len(valid)<3: continue
        w=(valid>0).mean()
        print(f'{h:>2d}天: {len(valid):>4d}笔 胜率{w:.0%} 均{valid.mean():+.1f}% 中位{valid.median():+.1f}%')

df.to_csv('/Users/ziruzhu/stock-tools/bull_returns.csv',index=False)
print('\n已保存')
