"""
S014 - 按月份(季节性)分析
按买入月份(1-12月)分组，看:
1. 各月份 Top1/Top2均权/Top3均权/Top20 的平均每期收益
2. 各月份"当期冠军"来自模型哪个名次(平均/中位)
"""
import json, sqlite3, ast, csv
import pandas as pd, numpy as np
from pathlib import Path
import warnings; warnings.filterwarnings("ignore")

S009 = Path("/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股")
S013 = Path("/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S013-喜神池LightGBM选股")
OUT  = Path("/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S014-Top精选集中策略")
DB   = "/Users/ziruzhu/stock-data/stock_all.db"
BUY_COST, SELL_COST = 0.00025, 0.00125

files = {"S009": S009/"trades_full.csv", "S013b": S013/"trades_s013b.csv"}

# 扫描
dates, codes = set(), set()
for fp in files.values():
    with open(fp, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            dates.add(str(int(row["buy_date"]))); dates.add(str(int(row["sell_date"])))
            for c in ast.literal_eval(row["holdings"]): codes.add(c)

print("查询价格 ...")
conn = sqlite3.connect(DB)
dph = ",".join(f"'{d}'" for d in dates); cph = ",".join(f"'{c}'" for c in codes)
price = pd.read_sql(f"SELECT ts_code,trade_date,open_qfq FROM stk_factor WHERE trade_date IN ({dph}) AND ts_code IN ({cph})", conn)
conn.close()
price["trade_date"] = price["trade_date"].astype(str)
price = price.set_index(["ts_code","trade_date"])["open_qfq"]

def op(code, d):
    try: return float(price.loc[(code, str(d))])
    except KeyError: return np.nan

def analyze_monthly(fp):
    df = pd.read_csv(fp, encoding="utf-8-sig")
    recs = []
    for _, row in df.iterrows():
        bd, sd = str(int(row["buy_date"])), str(int(row["sell_date"]))
        month = int(bd[4:6])  # 买入月份
        hs = ast.literal_eval(row["holdings"])
        rets = []
        for code in hs:
            e, x = op(code, bd), op(code, sd)
            r = (x/e - 1 - BUY_COST - SELL_COST) if (not np.isnan(e) and not np.isnan(x) and e != 0) else np.nan
            rets.append(r)
        valid = [(i, r) for i, r in enumerate(rets) if not np.isnan(r)]
        if not valid: continue
        # top1/2/3
        r1 = rets[0]
        top2 = np.nanmean(rets[:2]); top3 = np.nanmean(rets[:3]); top20 = np.nanmean(rets)
        # 冠军名次
        winner_rank = sorted(valid, key=lambda x:-x[1])[0][0] + 1
        recs.append({"month":month, "top1":r1, "top2":top2, "top3":top3, "top20":top20,
                     "winner_rank":winner_rank})
    d = pd.DataFrame(recs)
    # 按月聚合
    out = {}
    for m in range(1, 13):
        g = d[d["month"]==m]
        if len(g)==0:
            out[m] = None; continue
        out[m] = {
            "n": len(g),
            "top1_avg": float(g["top1"].mean()),
            "top2_avg": float(g["top2"].mean()),
            "top3_avg": float(g["top3"].mean()),
            "top20_avg": float(g["top20"].mean()),
            "top1_win": float((g["top1"]>0).mean()),
            "winner_rank_avg": float(g["winner_rank"].mean()),
            "winner_rank_med": float(g["winner_rank"].median()),
        }
    return out, d

print("分析 S009 月度 ...")
m009, raw009 = analyze_monthly(files["S009"])
print("分析 S013b 月度 ...")
m013, raw013 = analyze_monthly(files["S013b"])

MN = ["","1月","2月","3月","4月","5月","6月","7月","8月","9月","10月","11月","12月"]
def show(m, name):
    print(f"\n{'='*78}\n  {name} — 各买入月份 Top精选平均每期收益 & 冠军来源名次\n{'='*78}")
    print(f"  {'月份':<6}{'样本':<5}{'Top1':<9}{'Top2':<9}{'Top3':<9}{'Top20':<9}{'冠军均名次':<9}")
    for mo in range(1,13):
        s = m[mo]
        if s is None:
            print(f"  {MN[mo]:<6}  无数据"); continue
        print(f"  {MN[mo]:<6}{s['n']:<5}{s['top1_avg']*100:>6.2f}% {s['top2_avg']*100:>6.2f}% {s['top3_avg']*100:>6.2f}% {s['top20_avg']*100:>6.2f}%  第{s['winner_rank_avg']:.1f}名")

show(m009, "S009")
show(m013, "S013b")

json.dump({"s009":m009, "s013b":m013}, open(OUT/"s014_monthly_result.json","w"), ensure_ascii=False, indent=2)
print("\n已保存 s014_monthly_result.json")
