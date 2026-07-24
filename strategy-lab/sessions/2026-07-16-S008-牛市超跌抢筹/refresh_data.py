#!/usr/bin/env python3
"""S008 Dashboard 数据刷新"""
import sqlite3, json, pandas as pd, numpy as np

DB = '/Users/ziruzhu/stock-data/s008_track.db'
conn = sqlite3.connect(DB)

holdings = pd.read_sql('''SELECT ts_code, name, entry_date, entry_price, holding_days, return_pct, peak_return 
    FROM positions WHERE status='holding' ORDER BY return_pct DESC''', conn)

holdings['market'] = holdings['ts_code'].apply(lambda x: 
    '创业板' if x.startswith('300') else '科创板' if x.startswith('688') else 
    '深证' if x.startswith('00') else '上证')

nav = pd.read_sql('SELECT * FROM nav ORDER BY date', conn)
scans = pd.read_sql('SELECT * FROM scan_log ORDER BY scan_date DESC LIMIT 10', conn)

# 已平仓统计
closed_n = conn.execute("SELECT COUNT(*), COALESCE(AVG(return_pct),0) FROM positions WHERE status='closed'").fetchone()
closed_wr = conn.execute("SELECT COALESCE(AVG(CASE WHEN return_pct>0 THEN 1 ELSE 0 END),0) FROM positions WHERE status='closed'").fetchone()[0]

conn.close()

data = {
    'bull': '牛市', 'hs300': 4787, 'ma200': 4693,
    'total': len(holdings), 'avg_ret': round(holdings['return_pct'].mean(),1),
    'up': int((holdings['return_pct']>0).sum()), 'down': int((holdings['return_pct']<0).sum()),
    'closed_n': int(closed_n[0]), 'closed_ret': round(closed_n[1],1), 'closed_wr': round(closed_wr*100),
    'market_dist': holdings['market'].value_counts().to_dict(),
    'holdings': holdings.to_dict('records'),
    'nav': nav.to_dict('records'),
    'scans': scans.to_dict('records'),
}

with open('/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S008-牛市超跌抢筹/data.json', 'w') as f:
    json.dump(data, f, ensure_ascii=False, default=str)
print("✅ data.json 已刷新")
