import sqlite3, pandas as pd, numpy as np
import sys
sys.path.insert(0, "/Users/ziruzhu/stock-tools/strategy-lab/plan2_studio")
from config import DB_PATH, SCORE_DATE, DATA_LOAD_END, LOOKBACK
from features import make_features

code = "000001.SZ"  # 平安银行
conn = sqlite3.connect(DB_PATH)

print(f"诊断: {code}, SCORE_DATE={SCORE_DATE}, LOOKBACK={LOOKBACK}, DATA_LOAD_END={DATA_LOAD_END}")

g = pd.read_sql(f"SELECT ts_code,trade_date,open,high,low,close,vol,amount FROM daily "
                f"WHERE ts_code=? AND trade_date<='{DATA_LOAD_END}' ORDER BY trade_date DESC LIMIT {LOOKBACK+5}",
                conn, params=[code])
print(f"1. daily记录数: {len(g)}")
if len(g) == 0:
    print("  -> 无记录"); sys.exit()

g = g.sort_values("trade_date").reset_index(drop=True)
for c in ["open","high","low","close","vol","amount"]:
    g[c]=pd.to_numeric(g[c],errors="coerce")
g=g.dropna(subset=["close","high","low"]).query("close>0 and low>0").reset_index(drop=True)
print(f"2. 清洗后记录数: {len(g)}, 最新日期: {g['trade_date'].max()}")

idx = g.index[g["trade_date"]==SCORE_DATE]
print(f"3. trade_date==SCORE_DATE匹配: {len(idx)}条, idx={list(idx)}")
if len(idx)==0:
    print("  -> 没匹配上!"); sys.exit()

T = int(idx[0])
print(f"4. T={T}, 需要T >= {LOOKBACK+1}")
if T < LOOKBACK + 1:
    print("  -> T不够!"); sys.exit()

b = pd.read_sql(f"SELECT ts_code,trade_date,turnover_rate,pe_ttm,pb,ps_ttm,total_mv,circ_mv "
                f"FROM daily_basic WHERE ts_code=? AND trade_date<='{DATA_LOAD_END}' ORDER BY trade_date DESC LIMIT {LOOKBACK+5}",
                conn, params=[code])
for c2 in ["turnover_rate","pe_ttm","pb","ps_ttm","total_mv","circ_mv"]:
    b[c2]=pd.to_numeric(b[c2],errors="coerce")
b_idx=b.set_index(["ts_code","trade_date"])

mfg = pd.read_sql(f"SELECT ts_code,trade_date,buy_elg_amount,sell_elg_amount,buy_lg_amount,sell_lg_amount,net_mf_amount "
                  f"FROM moneyflow WHERE ts_code=? AND trade_date<='{DATA_LOAD_END}' ORDER BY trade_date DESC LIMIT {LOOKBACK+5}",
                  conn, params=[code])
for c3 in ["buy_elg_amount","sell_elg_amount","buy_lg_amount","sell_lg_amount","net_mf_amount"]:
    mfg[c3]=pd.to_numeric(mfg[c3],errors="coerce")
mfg["elg_net"]=mfg["buy_elg_amount"]-mfg["sell_elg_amount"]
mfg["lg_net"]=mfg["buy_lg_amount"]-mfg["sell_lg_amount"]
mfg=mfg.sort_values("trade_date").reset_index(drop=True)

tlg = pd.read_sql(f"SELECT trade_date,ts_code,net_amount FROM top_list "
                  f"WHERE ts_code=? AND trade_date<='{DATA_LOAD_END}' ORDER BY trade_date DESC LIMIT {LOOKBACK+5}",
                  conn, params=[code])
tlg["net_amount"]=pd.to_numeric(tlg["net_amount"],errors="coerce")
tlg=tlg.sort_values("trade_date").reset_index(drop=True)

print(f"5. b={len(b)}, mfg={len(mfg)}, tlg={len(tlg)}")

f = make_features(g, T, LOOKBACK, mfg, tlg, b_idx, code, {})
print(f"6. make_features返回: {'None' if f is None else 'ok'}")
if f is None:
    print("  -> make_features返回None!"); sys.exit()

# 检查特征名单
import pickle
with open("/Users/ziruzhu/stock-tools/strategy-lab/plan2_studio/plan2_meta.pkl","rb") as fp:
    meta = pickle.load(fp)
feat_names = meta["features"]
print(f"7. 模型特征数: {len(feat_names)}")
vals=[f.get(k,np.nan) for k in feat_names]
nan_feats = [feat_names[i] for i,v in enumerate(vals) if v is None or (isinstance(v,float) and np.isnan(v))]
print(f"8. NaN特征: {len(nan_feats)}个 -> {nan_feats}")
if nan_feats:
    print("  -> 因NaN被过滤掉! 这就是records为空的原因")
else:
    print("  ✅ 所有检查通过，这只票应该能进候选")
conn.close()
