"""
S014 - 按模型排名(rank)分析
1. 模型每个名次 rank1~20 的实际年化收益 -> 验证打分单调性
2. 每期"实际收益冠军/前3名"出现在模型排名的第几位 -> 分布
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

# ── 扫描需要的股票+日期 ──
dates, codes = set(), set()
files = {"S009": S009/"trades_full.csv", "S013b": S013/"trades_s013b.csv"}
for fp in files.values():
    with open(fp, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            dates.add(str(int(row["buy_date"]))); dates.add(str(int(row["sell_date"])))
            for c in ast.literal_eval(row["holdings"]): codes.add(c)

# ── 精准查价格 ──
print("查询价格 ...")
conn = sqlite3.connect(DB)
dph = ",".join(f"'{d}'" for d in dates)
cph = ",".join(f"'{c}'" for c in codes)
price = pd.read_sql(f"SELECT ts_code,trade_date,open_qfq FROM stk_factor WHERE trade_date IN ({dph}) AND ts_code IN ({cph})", conn)
conn.close()
price["trade_date"] = price["trade_date"].astype(str)
price = price.set_index(["ts_code","trade_date"])["open_qfq"]
print(f"价格行数: {len(price):,}")

def op(code, d):
    try: return float(price.loc[(code, str(d))])
    except KeyError: return np.nan

# ── 逐期逐名次计算 ──
def analyze(fp, name):
    df = pd.read_csv(fp, encoding="utf-8-sig")
    # rank_returns[rank] = 该名次每期收益list
    rank_rets = {r: [] for r in range(1, 21)}
    winner_rank = []   # 每期实际收益冠军的模型排名
    top3_ranks = []    # 每期实际收益前3名的模型排名(展开)
    per_period = []    # 每期各名次收益,用于算 top1/2/3

    for _, row in df.iterrows():
        bd, sd = str(int(row["buy_date"])), str(int(row["sell_date"]))
        hs = ast.literal_eval(row["holdings"])  # 已按score降序
        rets = []
        for i, code in enumerate(hs):
            e, x = op(code, bd), op(code, sd)
            r = (x/e - 1 - BUY_COST - SELL_COST) if (not np.isnan(e) and not np.isnan(x) and e != 0) else np.nan
            rets.append(r)
            if i < 20 and not np.isnan(r):
                rank_rets[i+1].append(r)
        per_period.append(rets)
        # 当期实际收益排序,找冠军和前3在模型里的名次
        valid = [(i, r) for i, r in enumerate(rets) if not np.isnan(r)]
        if valid:
            valid_sorted = sorted(valid, key=lambda x: -x[1])
            winner_rank.append(valid_sorted[0][0] + 1)  # 模型排名(1-based)
            for i, _ in valid_sorted[:3]:
                top3_ranks.append(i + 1)

    # 各名次统计
    n_periods = len(df)
    n_years = n_periods / 12
    rank_stats = {}
    for r in range(1, 21):
        s = pd.Series(rank_rets[r]).dropna()
        if len(s) == 0: continue
        nav = (1 + s).prod()
        ann = nav ** (1/n_years) - 1
        rank_stats[r] = {
            "n": len(s), "avg": float(s.mean()), "median": float(s.median()),
            "win_rate": float((s > 0).mean()), "annual": float(ann),
            "total": float(nav - 1), "std": float(s.std()),
        }
    return {
        "rank_stats": rank_stats,
        "winner_rank": winner_rank,
        "top3_ranks": top3_ranks,
        "n_periods": n_periods,
    }

print("\n分析 S009 ...")
r009 = analyze(files["S009"], "S009")
print("分析 S013b ...")
r013 = analyze(files["S013b"], "S013b")

# ── 打印各名次年化 ──
def show(res, name):
    print(f"\n{'='*70}\n  {name} — 模型各名次(rank)的实际年化收益\n{'='*70}")
    print(f"  {'名次':<6}{'年化':<10}{'期均':<10}{'中位':<10}{'胜率':<8}{'样本':<6}")
    for r in range(1, 21):
        if r not in res["rank_stats"]: continue
        s = res["rank_stats"][r]
        print(f"  rank{r:<3}{s['annual']*100:>7.1f}%{s['avg']*100:>9.2f}%{s['median']*100:>9.2f}%{s['win_rate']*100:>7.1f}%{s['n']:>6}")
    # 冠军来自哪个名次
    wr = pd.Series(res["winner_rank"])
    print(f"\n  【当期实际收益冠军】来自模型排名分布(共{len(wr)}期):")
    print(f"    冠军来自 Top3档: {(wr<=3).mean()*100:.1f}%  Top5档: {(wr<=5).mean()*100:.1f}%  Top10档: {(wr<=10).mean()*100:.1f}%")
    print(f"    冠军平均模型排名: 第{wr.mean():.1f}名  中位: 第{wr.median():.0f}名")
    # 前3名来自哪档
    t3 = pd.Series(res["top3_ranks"])
    print(f"  【当期实际收益前3名】来自模型排名分布:")
    print(f"    前3名来自 Top3档: {(t3<=3).mean()*100:.1f}%  Top5档: {(t3<=5).mean()*100:.1f}%  Top10档: {(t3<=10).mean()*100:.1f}%")

show(r009, "S009")
show(r013, "S013b")

# ── 保存 ──
json.dump({"s009": r009, "s013b": r013}, open(OUT/"s014_rank_result.json","w"), ensure_ascii=False, indent=2)
print("\n已保存 s014_rank_result.json")
