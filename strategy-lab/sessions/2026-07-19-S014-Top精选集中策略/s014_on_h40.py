"""
B：在40天长模型上重测 S014 头部跟投
用 s013_long_h40_result.json 的每期holdings(按score降序)，抽Top1/Top2/Top3，
计算各自从buy_date持有到sell_date(约40天)的收益，含-12%止损。
对比40天主仓(Top20)的年化41.9%。
"""
import json, sqlite3, bisect
import pandas as pd, numpy as np
from pathlib import Path
import warnings; warnings.filterwarnings("ignore")

OUT = Path("/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S014-Top精选集中策略")
DB = "/Users/ziruzhu/stock-data/stock_all.db"
BC, SC = 0.00025, 0.00125
STOP = -0.12

d = json.load(open(OUT / "s013_long_h40_result.json"))
trades = d["trades"]
main_ann = d["metrics"]["annual_return"]
print(f"40天主仓: 年化{main_ann*100:.1f}% 回撤{d['metrics']['max_drawdown']*100:.1f}% 期数{len(trades)}")

# 扫描
codes, dates = set(), set()
for t in trades:
    dates.add(t["buy_date"]); dates.add(t["sell_date"])
    for c in t["holdings"][:3]:
        codes.add(c)

conn = sqlite3.connect(DB)
all_td = pd.read_sql("SELECT DISTINCT trade_date FROM stk_factor ORDER BY trade_date", conn)["trade_date"].astype(str).tolist()
# 查询区间价格(需要日内low判断止损)
min_d, max_d = min(dates), max(dates)
cph = ",".join(f"'{c}'" for c in codes)
pf = pd.read_sql(f"SELECT ts_code,trade_date,open_qfq,low_qfq FROM stk_factor WHERE ts_code IN ({cph}) AND trade_date>='{min_d}' AND trade_date<='{max_d}'", conn)
conn.close()
pf["trade_date"] = pf["trade_date"].astype(str)
op_idx = pf.set_index(["ts_code","trade_date"])["open_qfq"]
lo_idx = pf.set_index(["ts_code","trade_date"])["low_qfq"]

def op(c,dt):
    try: return float(op_idx.loc[(c,dt)])
    except KeyError: return np.nan
def lo(c,dt):
    try: return float(lo_idx.loc[(c,dt)])
    except KeyError: return np.nan

def ret_with_stop(code, bd, sd):
    entry = op(code, bd)
    if np.isnan(entry) or entry<=0: return np.nan
    lo_i = bisect.bisect_right(all_td, bd)
    hi_i = bisect.bisect_right(all_td, sd)
    for dt in all_td[lo_i:hi_i]:
        l = lo(code, dt)
        if not np.isnan(l) and l/entry-1 <= STOP:
            return STOP - BC - SC
    ex = op(code, sd)
    if np.isnan(ex): return np.nan
    return ex/entry - 1 - BC - SC

N_YEARS = (pd.to_datetime(trades[-1]["sell_date"]) - pd.to_datetime(trades[0]["buy_date"])).days/365.25
PPY = 6  # 每2月调仓，年6期

def stats(rets, label):
    s = pd.Series([r for r in rets if not np.isnan(r)])
    nav = (1+s).cumprod()
    ann = nav.iloc[-1]**(1/N_YEARS)-1
    dd = ((nav-nav.cummax())/nav.cummax()).min()
    sh = s.mean()/s.std()*np.sqrt(PPY) if s.std()>0 else 0
    wr = (s>0).mean()
    print(f"  {label:<14} 年化{ann*100:>6.1f}%  回撤{dd*100:>6.1f}%  夏普{sh:>5.2f}  胜率{wr*100:>5.1f}%  期数{len(s)}")
    return dict(ann=ann, dd=dd, sharpe=sh, wr=wr)

top1, top2, top3, main = [], [], [], []
for t in trades:
    bd, sd = t["buy_date"], t["sell_date"]
    hs = t["holdings"]
    main.append(t["period_return"])
    r1 = ret_with_stop(hs[0], bd, sd)
    r2 = ret_with_stop(hs[1], bd, sd) if len(hs)>1 else np.nan
    r3 = ret_with_stop(hs[2], bd, sd) if len(hs)>2 else np.nan
    top1.append(r1)
    v2 = [x for x in [r1,r2] if not np.isnan(x)]; top2.append(np.mean(v2) if v2 else np.nan)
    v3 = [x for x in [r1,r2,r3] if not np.isnan(x)]; top3.append(np.mean(v3) if v3 else np.nan)

print("\n40天长模型上的头部跟投 (含-12%止损, 持仓约40天):")
stats(main, "主仓Top20")
stats(top1, "★ Top1单压")
stats(top2, "★ Top2均权")
stats(top3, "★ Top3均权")

# 保存明细供后续
det = pd.DataFrame({"buy_date":[t["buy_date"] for t in trades],
                    "sell_date":[t["sell_date"] for t in trades],
                    "main":main,"top1":top1,"top2":top2,"top3":top3})
det.to_csv(OUT/"s014_on_h40_detail.csv", index=False, encoding="utf-8-sig")
print("\n已保存 s014_on_h40_detail.csv")
