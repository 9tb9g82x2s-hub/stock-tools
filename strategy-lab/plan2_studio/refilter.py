"""重新过滤候选股:剔除ST + 亏损股"""
import sqlite3, pandas as pd

db="/Users/ziruzhu/stock-data/stock_all.db"
conn=sqlite3.connect(db)
bl_st=set(pd.read_sql("SELECT ts_code FROM blacklist_st",conn)['ts_code'])
bl_loss=set(pd.read_sql("SELECT ts_code FROM blacklist_loss",conn)['ts_code'])
conn.close()
blacklist=bl_st|bl_loss
print(f"黑名单:ST {len(bl_st)}只 + 亏损 {len(bl_loss)}只 = 合计{len(blacklist)}只")

base="/Users/ziruzhu/stock-tools/strategy-lab/plan2_studio"
for fname in ["candidates.csv","candidates_xishen.csv","candidates_top200.csv"]:
    df=pd.read_csv(f"{base}/{fname}")
    before=len(df)
    df=df[~df['ts_code'].isin(blacklist)].reset_index(drop=True)
    df.to_csv(f"{base}/{fname}",index=False)
    print(f"{fname}: {before}→{len(df)} (剔除{before-len(df)}只)")

# 重新打印喜神池干净版TOP30
xs=pd.read_csv(f"{base}/candidates_xishen.csv")
print(f"\n{'='*70}")
print(f"喜神池候选股 TOP30（剔除ST+亏损后，截至20260724）")
print(f"{'='*70}")
print(f"{'排名':<5}{'代码':<13}{'名称':<10}{'行业':<12}{'现价':>7}{'得分':>8}  {'评级'}")
print("-"*70)
for i,row in xs.head(30).iterrows():
    lvl="★★★" if row['score']>=0.55 else "★★" if row['score']>=0.45 else "★"
    print(f"{i+1:<5}{row['ts_code']:<13}{str(row.get('name','')):<10}{str(row.get('industry','')):<12}{row['close']:>7.2f}{row['score']:>8.4f}  {lvl}")

# 全量版
fa=pd.read_csv(f"{base}/candidates.csv")
print(f"\n{'='*70}")
print(f"全量候选股 TOP30（剔除ST+亏损后）")
print(f"{'='*70}")
print(f"{'排名':<5}{'代码':<13}{'名称':<10}{'行业':<12}{'现价':>7}{'得分':>8}  {'喜神':<6}{'评级'}")
print("-"*70)
for i,row in fa.head(30).iterrows():
    lvl="★★★" if row['score']>=0.55 else "★★" if row['score']>=0.45 else "★"
    xs_tag="✅" if row.get('in_xishen') else ""
    print(f"{i+1:<5}{row['ts_code']:<13}{str(row.get('name','')):<10}{str(row.get('industry','')):<12}{row['close']:>7.2f}{row['score']:>8.4f}  {xs_tag:<6}{lvl}")
