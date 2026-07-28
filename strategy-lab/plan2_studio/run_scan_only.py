"""
独立选股:加载已训练好的模型,只跑Step7扫描当前市场
不重跑全量训练
"""
import sqlite3, pandas as pd, lightgbm as lgb
from config import DB_PATH, EXCLUDE_BJ
from features import FEATURE_NAMES
from scan_market import scan_current_market_dbconn

print("加载已训练模型 plan2_model.txt ...")
model = lgb.Booster(model_file="/Users/ziruzhu/stock-tools/strategy-lab/plan2_studio/plan2_model.txt")

conn = sqlite3.connect(DB_PATH)
all_codes = pd.read_sql("SELECT DISTINCT ts_code FROM daily", conn)['ts_code'].tolist()
if EXCLUDE_BJ:
    all_codes = [c for c in all_codes if not c.endswith('.BJ')]
stock_list = pd.read_sql("SELECT ts_code,name,industry FROM stock_list", conn)
conn.close()
print(f"扫描 {len(all_codes)} 只票...")

scan_current_market_dbconn(model, DB_PATH, all_codes, stock_list, FEATURE_NAMES,
                           "/Users/ziruzhu/stock-tools/strategy-lab/plan2_studio")
print("\n选股完成")
