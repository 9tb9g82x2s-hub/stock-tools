"""
S013 持仓期扩展实验
A：每2/3个月调仓一次（真正降频）
B：每月选股但每批持有2个月（重叠持仓）

A的收益：上一次选出的Top20持有到下下期sell_date，用价格重算
B的收益：当期Top20 ∪ 上期Top20 合并等权持有一个月
"""
import ast, csv, sqlite3
import pandas as pd, numpy as np
import bisect
from datetime import datetime, timedelta
from pathlib import Path
import warnings; warnings.filterwarnings("ignore")

S013_CSV = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S013-喜神池LightGBM选股/trades_s013b.csv"
DB = "/Users/ziruzhu/stock-data/stock_all.db"
BC, SC = 0.00025, 0.00125

# ─── 读取所有期的 holdings / buy / sell ───
with open(S013_CSV, encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))
rows = sorted(rows, key=lambda r: int(r["buy_date"]))
N = len(rows)
N_YEARS = N / 12
print(f"S013b 期数: {N}")

buy_dates  = [str(int(r["buy_date"])) for r in rows]
sell_dates = [str(int(r["sell_date"])) for r in rows]
holdings   = [ast.literal_eval(r["holdings"]) for r in rows]
orig_rets  = [float(r["period_return"]) for r in rows]

# ─── 扫描所有需要查询的股票和日期 ───
# A需要额外的sell_date（跳2/3期后的卖出日）
all_codes, all_dates = set(), set()
for i, hs in enumerate(holdings):
    all_dates.add(buy_dates[i])
    all_dates.add(sell_dates[i])
    for c in hs:
        all_codes.add(c)

print(f"需要: 股票{len(all_codes)}只, 日期{len(all_dates)}个")

# ─── 精准查询价格 ───
print("查询价格...")
conn = sqlite3.connect(DB)
all_td = pd.read_sql("SELECT DISTINCT trade_date FROM stk_factor ORDER BY trade_date", conn)["trade_date"].astype(str).tolist()
dph = ",".join(f"'{d}'" for d in all_dates)
cph = ",".join(f"'{c}'" for c in all_codes)
price_df = pd.read_sql(
    f"SELECT ts_code, trade_date, open_qfq FROM stk_factor WHERE trade_date IN ({dph}) AND ts_code IN ({cph})", conn
)
conn.close()
price_df["trade_date"] = price_df["trade_date"].astype(str)
pidx = price_df.set_index(["ts_code","trade_date"])["open_qfq"]
print(f"价格行数: {len(pidx):,}")

def op(code, d):
    try: return float(pidx.loc[(code, str(d))])
    except KeyError: return np.nan

def calc_port_return(hs, bd, sd):
    """计算一组股票等权持有从bd到sd的组合收益（含手续费）"""
    rets = []
    for code in hs:
        e, x = op(code, bd), op(code, sd)
        if not np.isnan(e) and not np.isnan(x) and e > 0:
            rets.append(x/e - 1 - BC - SC)
    return float(np.mean(rets)) if rets else np.nan

# ─── 基准：月度换仓原始数据 ───
base_nav = (1 + pd.Series(orig_rets)).cumprod()
base_ann = (base_nav.iloc[-1]) ** (1/N_YEARS) - 1
base_dd  = ((base_nav - base_nav.cummax())/base_nav.cummax()).min()
print(f"\n基准(月度): 年化{base_ann*100:.1f}% 回撤{base_dd*100:.1f}%")

def summarize(rets_list, label):
    s = pd.Series([r for r in rets_list if not np.isnan(r)])
    if len(s) == 0:
        print(f"{label}: 无有效数据")
        return
    n_y = len(rets_list) / 12  # 原始时间跨度不变
    nav = (1 + s).cumprod()
    ann = nav.iloc[-1] ** (1/n_y) - 1
    dd  = ((nav - nav.cummax())/nav.cummax()).min()
    wr  = (s > 0).mean()
    sh  = s.mean()/s.std()*(12**0.5) if s.std()>0 else 0
    print(f"{label}: 期数{len(s)}  年化{ann*100:.1f}%  回撤{dd*100:.1f}%  夏普{sh:.2f}  胜率{wr*100:.1f}%")

print("\n" + "="*65)
print("  做法A — 降低调仓频率（每N个月才换股）")
print("="*65)

# A2: 每2个月调仓（取第0,2,4,6...期，持到下次调仓的sell_date）
for skip in [2, 3]:
    adj_rows = []
    i = 0
    while i < N:
        next_i = i + skip
        # 持仓：第i期的Top20，从buy_dates[i]持到sell_dates[min(next_i-1,N-1)]
        sd = sell_dates[min(next_i-1, N-1)]
        bd = buy_dates[i]
        hs = holdings[i]
        r  = calc_port_return(hs, bd, sd)
        adj_rows.append(r)
        i = next_i
    # 时间跨度和原来一样，但期数少了
    n_y_real = N_YEARS  # 总年数不变
    s = pd.Series([r for r in adj_rows if not np.isnan(r)])
    nav = (1+s).cumprod()
    ann = nav.iloc[-1] ** (1/n_y_real) - 1
    dd  = ((nav-nav.cummax())/nav.cummax()).min()
    wr  = (s>0).mean()
    sh  = s.mean()/s.std()*(12**0.5) if s.std()>0 else 0
    print(f"  每{skip}个月调仓: 期数{len(s)}  年化{ann*100:.1f}%  回撤{dd*100:.1f}%  夏普{sh:.2f}  胜率{wr*100:.1f}%")

print("\n" + "="*65)
print("  做法B — 重叠持仓（每月选股，合并当期+上期持仓）")
print("="*65)
# B: 合并当期holdings[i]和上期holdings[i-1]，等权持有一个月
b_rets = []
for i in range(N):
    combined = list(set(holdings[i]))
    if i > 0:
        combined = list(set(holdings[i] + holdings[i-1]))
    r = calc_port_return(combined, buy_dates[i], sell_dates[i])
    b_rets.append(r)
summarize(b_rets, "  当期+上期合并(等权)")

# B2：合并当期+上期，但上期权重降低（2/3当期+1/3上期，按股数近似）
b2_rets = []
for i in range(N):
    if i == 0:
        hs = holdings[i]
        r = calc_port_return(hs, buy_dates[i], sell_dates[i])
    else:
        # 当期票各1份，上期票各1份，但上期和当期重叠的票算2份（超配）
        # 等效于：重叠票权重2x，非重叠权重1x
        curr = set(holdings[i])
        prev = set(holdings[i-1])
        overlap = curr & prev
        new_curr = curr - prev
        new_prev = prev - curr
        # 权重：overlap=2, new_curr=1, new_prev=1
        total_w = len(overlap)*2 + len(new_curr) + len(new_prev)
        rets = []
        for c in overlap:
            e,x = op(c,buy_dates[i]),op(c,sell_dates[i])
            if not np.isnan(e) and not np.isnan(x) and e>0:
                rets += [x/e-1-BC-SC]*2
        for c in new_curr|new_prev:
            e,x = op(c,buy_dates[i]),op(c,sell_dates[i])
            if not np.isnan(e) and not np.isnan(x) and e>0:
                rets.append(x/e-1-BC-SC)
        r = float(np.mean(rets)) if rets else np.nan
    b2_rets.append(r)
summarize(b2_rets, "  重叠超配（overlap权重2x）")

print("\n" + "="*65)
print("  汇总对比（全部同一时间段 2017-2026）")
print("="*65)
print(f"  基准月度换仓:     年化{base_ann*100:.1f}%  回撤{base_dd*100:.1f}%")
