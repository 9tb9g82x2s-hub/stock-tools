"""
S014 - Top精选集中策略分析
比较 S009 / S013b 原策略(Top20等权) vs Top1 / Top2均权 / Top3均权 的收益表现
目的：评估是否有足够收益支撑"另设一组仓位跟投 Top1~3"
"""

import json
import sqlite3
import ast
import pandas as pd
import numpy as np
from pathlib import Path
import warnings
warnings.filterwarnings("ignore")

# ─────────────────────── 路径配置 ───────────────────────
S009_DIR = Path("/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股")
S013_DIR = Path("/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S013-喜神池LightGBM选股")
OUT_DIR  = Path("/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S014-Top精选集中策略")
DB_PATH  = "/Users/ziruzhu/stock-data/stock_all.db"

# 手续费（单边）：佣金0.025% + 卖出印花税0.1%，买+卖≈0.15%
BUY_COST  = 0.00025
SELL_COST = 0.00125   # 0.00025佣金 + 0.001印花税

print("=" * 60)
print("S014 Top精选集中策略分析")
print("=" * 60)

# ─────────────────────── 预扫描所需股票和日期 ───────────────────────
print("\n[1] 预扫描需要的股票和日期 ...")
import csv as _csv

def scan_needed(filepath):
    dates_set, codes_set = set(), set()
    with open(filepath, encoding="utf-8-sig") as f:
        for row in _csv.DictReader(f):
            dates_set.add(str(int(row["buy_date"])))
            dates_set.add(str(int(row["sell_date"])))
            hs = ast.literal_eval(row["holdings"])
            for c in hs[:3]:
                codes_set.add(c)
    return dates_set, codes_set

d1, c1 = scan_needed(S009_DIR / "trades_full.csv")
d2, c2 = scan_needed(S013_DIR / "trades_s013b.csv")
all_dates = sorted(d1 | d2)
all_codes = sorted(c1 | c2)
print(f"  需要日期: {len(all_dates)}个  需要股票: {len(all_codes)}只")

# ─────────────────────── 精准查询价格数据 ───────────────────────
print("\n  精准查询stk_factor价格 ...")
dates_ph = ",".join(f"'{d}'" for d in all_dates)
codes_ph = ",".join(f"'{c}'" for c in all_codes)
conn = sqlite3.connect(DB_PATH)
price_df = pd.read_sql(
    f"SELECT ts_code, trade_date, open_qfq FROM stk_factor "
    f"WHERE trade_date IN ({dates_ph}) AND ts_code IN ({codes_ph})",
    conn
)
conn.close()
price_df["trade_date"] = price_df["trade_date"].astype(str)
price_df = price_df.set_index(["ts_code", "trade_date"])
print(f"  价格行数: {len(price_df):,}  股票数: {price_df.index.get_level_values('ts_code').nunique():,}")

def get_open(ts_code, date):
    try:
        return float(price_df.loc[(ts_code, str(date)), "open_qfq"])
    except KeyError:
        return np.nan

# ─────────────────────── 读取S009交易记录 ───────────────────────
print("\n[2] 加载 S009 交易记录 ...")
s009_trades = pd.read_csv(S009_DIR / "trades_full.csv", encoding="utf-8-sig")
print(f"  期数: {len(s009_trades)}")

def parse_holdings(h):
    """解析holdings字段 -> list"""
    if isinstance(h, list):
        return h
    try:
        return ast.literal_eval(h)
    except Exception:
        return []

# ─────────────────────── 读取S013b交易记录 ───────────────────────
print("\n[3] 加载 S013b 交易记录 ...")
s013_trades = pd.read_csv(S013_DIR / "trades_s013b.csv", encoding="utf-8-sig")
print(f"  期数: {len(s013_trades)}")

# ─────────────────────── 核心计算函数 ───────────────────────
def calc_top_returns(trades_df, strategy_name):
    """
    对每一期计算:
    - top1, top2(均权), top3(均权) 的个股收益
    - 与原策略等权收益对比
    """
    results = []
    skip_count = 0

    for _, row in trades_df.iterrows():
        buy_date  = str(int(row["buy_date"]))
        sell_date = str(int(row["sell_date"]))
        period_return = row["period_return"]   # 原策略已含手续费
        holdings = parse_holdings(row["holdings"])

        if len(holdings) < 3:
            skip_count += 1
            continue

        top_stocks = holdings[:3]  # [top1, top2, top3]

        # 计算每只股票收益（不含止损保护，使用sell_date开盘价）
        stock_rets = []
        missing = 0
        for code in top_stocks:
            entry = get_open(code, buy_date)
            exit_ = get_open(code, sell_date)
            if np.isnan(entry) or np.isnan(exit_) or entry == 0:
                missing += 1
                stock_rets.append(np.nan)
            else:
                r = (exit_ / entry - 1) - BUY_COST - SELL_COST
                stock_rets.append(r)

        r1, r2, r3 = stock_rets

        # top1 收益 = 只买排名第1的股票
        top1_ret = r1

        # top2 收益 = 只买排名前2等权
        valid_top2 = [x for x in [r1, r2] if not np.isnan(x)]
        top2_ret = np.mean(valid_top2) if valid_top2 else np.nan

        # top3 收益 = 只买排名前3等权
        valid_top3 = [x for x in [r1, r2, r3] if not np.isnan(x)]
        top3_ret = np.mean(valid_top3) if valid_top3 else np.nan

        results.append({
            "strategy": strategy_name,
            "rebalance_date": row["rebalance_date"],
            "buy_date": buy_date,
            "sell_date": sell_date,
            "n_holdings": row["n_holdings"],
            "original_return": period_return,  # Top20等权含手续费
            "top1_code": top_stocks[0],
            "top2_code": top_stocks[1],
            "top3_code": top_stocks[2],
            "top1_return": top1_ret,
            "top2_return": top2_ret,
            "top3_return": top3_ret,
            "missing_prices": missing,
        })

    print(f"  {strategy_name}: 计算{len(results)}期，跳过{skip_count}期")
    return pd.DataFrame(results)

# ─────────────────────── 执行计算 ───────────────────────
print("\n[4] 计算 S009 Top精选收益 ...")
df009 = calc_top_returns(s009_trades, "S009")

print("\n[5] 计算 S013b Top精选收益 ...")
df013 = calc_top_returns(s013_trades, "S013b")

# ─────────────────────── 统计分析函数 ───────────────────────
def analyze_strategy(df, name):
    """汇总统计：胜率、均值、中位数、分位数、NAV曲线"""
    stats = {}
    for col in ["original_return", "top1_return", "top2_return", "top3_return"]:
        s = df[col].dropna()
        if len(s) == 0:
            continue
        # 累乘NAV
        nav = (1 + s).cumprod()
        total_ret = nav.iloc[-1] - 1
        n_years = len(s) / 12  # 每期约1个月
        annual_ret = (1 + total_ret) ** (1 / n_years) - 1 if n_years > 0 else 0
        # 最大回撤
        rolling_max = nav.cummax()
        drawdown = (nav - rolling_max) / rolling_max
        max_dd = drawdown.min()
        stats[col] = {
            "n_periods": len(s),
            "win_rate": (s > 0).mean(),
            "avg_return": s.mean(),
            "median_return": s.median(),
            "p25": s.quantile(0.25),
            "p75": s.quantile(0.75),
            "total_return": total_ret,
            "annual_return": annual_ret,
            "max_drawdown": max_dd,
            "std": s.std(),
            "sharpe": s.mean() / s.std() * (12 ** 0.5) if s.std() > 0 else 0,
        }
    return stats

print("\n[6] 汇总统计 ...")
stats009 = analyze_strategy(df009, "S009")
stats013 = analyze_strategy(df013, "S013b")

# ─────────────────────── 打印结果 ───────────────────────
def print_stats(stats, name):
    print(f"\n{'='*50}")
    print(f"  {name} - Top精选 vs 原策略对比")
    print(f"{'='*50}")
    labels = {
        "original_return": "原策略Top20",
        "top1_return":     "★ Top1只",
        "top2_return":     "★ Top2均权",
        "top3_return":     "★ Top3均权",
    }
    for key, label in labels.items():
        if key not in stats:
            continue
        s = stats[key]
        print(f"\n  {label}")
        print(f"    年化收益: {s['annual_return']:+.1%}  总收益: {s['total_return']:+.1%}")
        print(f"    胜率:     {s['win_rate']:.1%}  均收益/期: {s['avg_return']:+.2%}  中位数: {s['median_return']:+.2%}")
        print(f"    最大回撤: {s['max_drawdown']:.1%}  夏普: {s['sharpe']:.2f}")

print_stats(stats009, "S009")
print_stats(stats013, "S013b")

# ─────────────────────── 保存结果 ───────────────────────
df009.to_csv(OUT_DIR / "s014_top_detail_s009.csv", index=False, encoding="utf-8-sig")
df013.to_csv(OUT_DIR / "s014_top_detail_s013b.csv", index=False, encoding="utf-8-sig")

result_json = {
    "s009": {k: {sk: round(float(sv), 6) for sk, sv in v.items()} for k, v in stats009.items()},
    "s013b": {k: {sk: round(float(sv), 6) for sk, sv in v.items()} for k, v in stats013.items()},
}
with open(OUT_DIR / "s014_result.json", "w") as f:
    json.dump(result_json, f, ensure_ascii=False, indent=2)

print("\n[7] 数据已保存到:", str(OUT_DIR))
print("  - s014_top_detail_s009.csv")
print("  - s014_top_detail_s013b.csv")
print("  - s014_result.json")
print("\n分析完成！")
