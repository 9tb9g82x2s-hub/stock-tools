"""
S014 持仓期 × 止损 矩阵分析
对 S013b 每期 Top1、Top2，分别测试:
  持仓期: 月度换仓(按原始sell_date) / 30日历天 / 60日历天 / 80日历天
  止损: 无止损 / -5% / -8% / -10% / -12%
输出: 年化收益 × 最大回撤 × 胜率 热力矩阵
"""
import ast, csv, sqlite3
import pandas as pd, numpy as np
from datetime import datetime, timedelta
from pathlib import Path
import warnings; warnings.filterwarnings("ignore")

S013_CSV = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S013-喜神池LightGBM选股/trades_s013b.csv"
DB = "/Users/ziruzhu/stock-data/stock_all.db"
OUT = Path("/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S014-Top精选集中策略")
BC, SC = 0.00025, 0.00125  # 买入/卖出手续费

# ─── 读取交易数据 ───
with open(S013_CSV, encoding="utf-8-sig") as f:
    trades = list(csv.DictReader(f))
print(f"S013b 期数: {len(trades)}")

# ─── 收集所有需要的股票和日期 ───
allcodes = set()
alldates_needed = set()
for r in trades:
    bd = str(int(r["buy_date"]))
    alldates_needed.add(bd)
    alldates_needed.add(str(int(r["sell_date"])))
    for c in ast.literal_eval(r["holdings"])[:2]:
        allcodes.add(c)

# ─── 加载价格 (open_qfq, low_qfq) 和交易日历 ───
print("加载价格 ...")
conn = sqlite3.connect(DB)
# 交易日历：用全表 DISTINCT trade_date（stk_factor 不含指数，必须用个股日期）
all_trade_dates = pd.read_sql(
    "SELECT DISTINCT trade_date FROM stk_factor ORDER BY trade_date", conn
)["trade_date"].astype(str).tolist()
print(f"交易日历天数: {len(all_trade_dates)}")

# 扩大查询范围：buy_date + 120天以内的价格
min_date = min(alldates_needed)
max_bd = max(str(int(r["buy_date"])) for r in trades)
max_date_needed = (datetime.strptime(max_bd, "%Y%m%d") + timedelta(days=120)).strftime("%Y%m%d")

cph = ",".join(f"'{c}'" for c in allcodes)
price_df = pd.read_sql(
    f"SELECT ts_code, trade_date, open_qfq, low_qfq, close_qfq FROM stk_factor "
    f"WHERE ts_code IN ({cph}) AND trade_date >= '{min_date}' AND trade_date <= '{max_date_needed}'",
    conn
)
conn.close()
price_df["trade_date"] = price_df["trade_date"].astype(str)
open_idx = price_df.set_index(["ts_code", "trade_date"])["open_qfq"]
low_idx = price_df.set_index(["ts_code", "trade_date"])["low_qfq"]
close_idx = price_df.set_index(["ts_code", "trade_date"])["close_qfq"]
print(f"价格行数: {len(open_idx):,}")

# ─── 工具函数 ───
def get_open(code, date_str):
    try: return float(open_idx.loc[(code, date_str)])
    except KeyError: return np.nan

def get_low(code, date_str):
    try: return float(low_idx.loc[(code, date_str)])
    except KeyError: return np.nan

# 交易日历建索引，加速 N 天后查找
_td_arr = all_trade_dates
def find_date_after_n_days(buy_date_str, n_days):
    """找 buy_date 之后 n_days 日历天后最近的交易日"""
    target = (datetime.strptime(buy_date_str, "%Y%m%d") + timedelta(days=n_days)).strftime("%Y%m%d")
    import bisect
    i = bisect.bisect_left(_td_arr, target)
    return _td_arr[i] if i < len(_td_arr) else None

def calc_return_with_stop(code, buy_date, sell_date, stop_loss, hold_days=None):
    """
    计算单笔收益，含止损:
    - 每个交易日用当日最低价(low_qfq)检查是否触及止损线
    - 触及则按止损价(entry*(1+stop_loss))成交，更贴近实盘
    - hold_days: None=用原始sell_date; 否则用buy_date+hold_days日历天推算sell_date
    - stop_loss: 如 -0.08 表示 -8%
    """
    entry = get_open(code, buy_date)
    if np.isnan(entry) or entry == 0:
        return np.nan

    # 确定实际卖出日期
    if hold_days is None:
        actual_sell = sell_date
    else:
        actual_sell = find_date_after_n_days(buy_date, hold_days)
        if actual_sell is None:
            actual_sell = sell_date

    # 获取 buy_date 到 actual_sell 之间所有交易日（按顺序）
    import bisect
    lo = bisect.bisect_right(_td_arr, buy_date)
    hi = bisect.bisect_right(_td_arr, actual_sell)
    check_dates = _td_arr[lo:hi]

    # 逐日检查止损（用当日最低价判断是否触及止损线）
    if stop_loss is not None:
        for d in check_dates:
            lw = get_low(code, d)
            if np.isnan(lw): continue
            if lw / entry - 1 <= stop_loss:
                # 触及止损线，按止损价成交（假设能在止损价卖出）
                return stop_loss - BC - SC

    # 未触发止损，以实际卖出日开盘价卖出
    exit_px = get_open(code, actual_sell)
    if np.isnan(exit_px):
        return np.nan
    return exit_px / entry - 1 - BC - SC

# ─── 矩阵参数 ───
HOLD_OPTIONS = {
    "月度换仓": None,
    "30天": 30,
    "60天": 60,
    "80天": 80,
}
STOP_OPTIONS = {
    "无止损": None,
    "-5%": -0.05,
    "-8%": -0.08,
    "-10%": -0.10,
    "-12%": -0.12,
}
TOPS = {"Top1": 0, "Top2": 1}

# ─── 主循环 ───
results = []
n_years = len(trades) / 12

print("\n开始计算矩阵 ...")
for top_name, top_idx in TOPS.items():
    for hold_name, hold_days in HOLD_OPTIONS.items():
        for stop_name, stop_loss in STOP_OPTIONS.items():
            rets = []
            for r in trades:
                bd = str(int(r["buy_date"]))
                sd = str(int(r["sell_date"]))
                hs = ast.literal_eval(r["holdings"])
                if top_idx >= len(hs): continue

                if top_name == "Top2":
                    # Top2均权 = Top1 + Top2 各算一笔，取平均
                    r1 = calc_return_with_stop(hs[0], bd, sd, stop_loss, hold_days)
                    r2 = calc_return_with_stop(hs[1], bd, sd, stop_loss, hold_days) if len(hs) > 1 else np.nan
                    valid = [x for x in [r1, r2] if not np.isnan(x)]
                    ret = np.mean(valid) if valid else np.nan
                else:
                    ret = calc_return_with_stop(hs[top_idx], bd, sd, stop_loss, hold_days)

                rets.append(ret)

            s = pd.Series(rets).dropna()
            if len(s) == 0: continue
            nav = (1 + s).cumprod()
            total = nav.iloc[-1] - 1
            annual = (1 + total) ** (1 / n_years) - 1
            dd = ((nav - nav.cummax()) / nav.cummax()).min()
            sharpe = s.mean() / s.std() * (12 ** 0.5) if s.std() > 0 else 0
            results.append({
                "top": top_name,
                "hold": hold_name,
                "stop": stop_name,
                "annual": round(annual * 100, 1),
                "total": round(total * 100, 1),
                "max_dd": round(dd * 100, 1),
                "win_rate": round((s > 0).mean() * 100, 1),
                "sharpe": round(sharpe, 2),
                "avg_per": round(s.mean() * 100, 2),
                "n": len(s),
            })

df_res = pd.DataFrame(results)
df_res.to_csv(OUT / "s014_matrix_result.csv", index=False, encoding="utf-8-sig")

# ─── 打印关键结果 ───
for top in ["Top1", "Top2"]:
    print(f"\n{'='*72}\n  {top} — 年化收益矩阵 (%)\n{'='*72}")
    sub = df_res[df_res["top"] == top]
    pivot = sub.pivot(index="hold", columns="stop", values="annual")
    pivot = pivot.reindex(list(HOLD_OPTIONS.keys()))
    pivot = pivot.reindex(list(STOP_OPTIONS.keys()), axis=1)
    print(pivot.to_string())
    print(f"\n  {top} — 最大回撤矩阵 (%)")
    pivot2 = sub.pivot(index="hold", columns="stop", values="max_dd")
    pivot2 = pivot2.reindex(list(HOLD_OPTIONS.keys()))
    pivot2 = pivot2.reindex(list(STOP_OPTIONS.keys()), axis=1)
    print(pivot2.to_string())
    print(f"\n  {top} — 夏普矩阵")
    pivot3 = sub.pivot(index="hold", columns="stop", values="sharpe")
    pivot3 = pivot3.reindex(list(HOLD_OPTIONS.keys()))
    pivot3 = pivot3.reindex(list(STOP_OPTIONS.keys()), axis=1)
    print(pivot3.to_string())

print(f"\n结果已保存: s014_matrix_result.csv")
