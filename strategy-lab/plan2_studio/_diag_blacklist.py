import sqlite3, pandas as pd
conn = sqlite3.connect("/Users/ziruzhu/stock-data/stock_all.db")
for t in ['blacklist_st','blacklist_loss']:
    cols=[r[1] for r in conn.execute(f"PRAGMA table_info({t})").fetchall()]
    n=conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"\n[{t}] 行数={n} 列={cols}")
    print(pd.read_sql(f"SELECT * FROM {t} LIMIT 5", conn).to_string())
conn.close()
