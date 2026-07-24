#!/usr/bin/env python3
"""
增量更新 stock_all.db 日线数据
用 akshare 下载最近N天的数据
"""
import sqlite3, pandas as pd, numpy as np, time, sys, os
DB = os.path.expanduser('~/stock-data/stock_all.db')

def update_daily(codes=None, days=30):
    """更新日线数据，默认更新最近30天"""
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    
    # 获取所有需要更新的股票代码
    if codes is None:
        cur.execute("SELECT DISTINCT ts_code FROM daily")
        codes = [r[0] for r in cur.fetchall()]
    
    # 获取数据库中最新的日期
    cur.execute("SELECT MAX(trade_date) FROM daily")
    latest = cur.fetchone()[0]
    print(f"数据库最新日期: {latest}")
    print(f"股票数: {len(codes)}只")
    
    # 需要从 latest+1 开始补
    from datetime import datetime, timedelta
    start_date = datetime.strptime(latest, '%Y%m%d') + timedelta(days=1)
    
    # 如果start_date以后没有交易日(比如今天是周六),就跳过
    today = datetime.now()
    if start_date > today:
        print(f"数据已是最新({latest}), 无需更新")
        conn.close()
        return
    
    start_str = start_date.strftime('%Y%m%d')
    end_str = today.strftime('%Y%m%d')
    print(f"补数据范围: {start_str} ~ {end_str}")
    
    # 批量下载(akshare 分批,每批50只)
    batch_size = 50
    total_batches = (len(codes) + batch_size - 1) // batch_size
    
    print(f"分批下载({batch_size}只/批, 共{total_batches}批)...")
    
    try:
        import akshare as ak
    except ImportError:
        print("akshare未安装, 尝试安装...")
        os.system(f"{sys.executable} -m pip install akshare -q")
        import akshare as ak
    
    new_count = 0
    fail_count = 0
    
    for batch_idx in range(0, len(codes), batch_size):
        batch_codes = codes[batch_idx:batch_idx+batch_size]
        batch_num = batch_idx // batch_size + 1
        
        for code in batch_codes:
            try:
                # 去掉后缀
                pure_code = code.split('.')[0]
                market = code.split('.')[1]
                
                # 用akshare获取日线
                if market == 'SH':
                    symbol = f"sh{pure_code}"
                elif market == 'SZ':
                    symbol = f"sz{pure_code}"
                elif market == 'BJ':
                    symbol = f"bj{pure_code}"
                else:
                    continue
                
                df = ak.stock_zh_a_hist(
                    symbol=pure_code,
                    period="daily",
                    start_date=start_str.replace('20', ''),
                    end_date=end_str.replace('20', ''),
                    adjust="qfq"
                )
                
                if df is None or len(df) == 0:
                    continue
                
                # 写入数据库
                for _, row in df.iterrows():
                    trade_date = row['日期'].replace('-', '')
                    cur.execute("""
                        INSERT OR REPLACE INTO daily 
                        (ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        code, trade_date,
                        row['开盘'], row['最高'], row['最低'], row['收盘'],
                        None, None, row.get('涨跌幅', None),
                        row['成交量'], row['成交额']
                    ))
                    new_count += 1
                
                time.sleep(0.1)  # 避免太快
                
            except Exception as e:
                fail_count += 1
                if fail_count <= 3:
                    print(f"  {code} 失败: {e}")
                time.sleep(0.5)
        
        if batch_num % 10 == 0:
            conn.commit()
            print(f"  进度: {batch_num}/{total_batches}批 ({new_count}条新增, {fail_count}条失败)")
        
        time.sleep(1)  # 批次间休息
    
    conn.commit()
    conn.close()
    
    print(f"\n完成! 新增{new_count}条, 失败{fail_count}条")

if __name__ == '__main__':
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    update_daily(days=days)
