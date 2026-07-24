#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S012 快速验证 · 喜神(金水)股票 vs 非喜神股票 的历史表现对比

思路：不重训模型。直接拆解 S009 已有的113期持仓——
把每期20只票按"是否在喜神池(金水1485只)"分两组，
用前复权开盘价算每只票 buy_date→sell_date 的真实收益，
统计喜神票 vs 非喜神票的平均收益、胜率、以及"只买喜神票"的组合净值。

回答：金水喜神股在 LightGBM 选出来的票里，本来就跑得更好还是更差？
"""
import pandas as pd, numpy as np, sqlite3, ast, time

BASE = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股"
DB = "/Users/ziruzhu/stock-data/stock_all.db"
POOL = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-18-S012-五行月份LightGBM选股/xishen_pool.csv"

BUY_COMM = 0.00025
SELL_COMM = 0.00025
STAMP = 0.0005


def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def main():
    t0 = time.time()
    # 喜神池
    pool = pd.read_csv(POOL)
    xishen = set(pool["ts_code"])
    wuxing_map = dict(zip(pool["ts_code"], pool["主五行"]))
    log(f"喜神池: {len(xishen)} 只 (金{(pool['主五行']=='金').sum()}/水{(pool['主五行']=='水').sum()})")

    # 113期持仓
    t = pd.read_csv(f"{BASE}/trades_full.csv").sort_values("rebalance_date").reset_index(drop=True)
    log(f"S009持仓期数: {len(t)}")

    # 收集所有票+日期范围，预加载前复权价
    all_codes = set()
    for _, r in t.iterrows():
        all_codes.update(ast.literal_eval(r["holdings"]))
    min_d, max_d = str(t["buy_date"].min()), str(t["sell_date"].max())
    log(f"涉及{len(all_codes)}只票, 日期{min_d}~{max_d}, 预加载前复权价...")

    con = sqlite3.connect(DB)
    codes_str = ",".join(f"'{c}'" for c in all_codes)
    px = pd.read_sql(
        f"SELECT ts_code, trade_date, open_qfq FROM stk_factor "
        f"WHERE trade_date BETWEEN '{min_d}' AND '{max_d}' AND ts_code IN ({codes_str})", con)
    con.close()
    px["open_qfq"] = pd.to_numeric(px["open_qfq"], errors="coerce")
    open_lk = px.set_index(["ts_code", "trade_date"])["open_qfq"]
    log(f"日线记录 {len(px):,} 行")

    def stock_ret(code, bd, sd):
        try:
            p0 = open_lk.loc[(code, bd)]; p1 = open_lk.loc[(code, sd)]
            if pd.notna(p0) and pd.notna(p1) and p0 > 0:
                return float(p1)/float(p0) - 1
        except KeyError:
            return None
        return None

    # ---- 逐期逐票拆解 ----
    xs_rets_all, non_rets_all = [], []      # 全部单票收益(不分期)
    period_rows = []                         # 每期分组均值
    # 三条组合净值曲线
    nav_all, nav_xs, nav_non = 1.0, 1.0, 1.0
    curve_all, curve_xs, curve_non = [1.0], [1.0], [1.0]
    prev_all, prev_xs, prev_non = set(), set(), set()

    def cost(curr, prev):
        c, p = set(curr), set(prev)
        n_c = len(c) or 1; n_p = len(p) or 1
        buy_to = len(c - p)/n_c; sell_to = len(p - c)/n_p
        return buy_to*BUY_COMM + sell_to*(SELL_COMM+STAMP)

    for _, r in t.iterrows():
        codes = ast.literal_eval(r["holdings"])
        bd, sd = str(r["buy_date"]), str(r["sell_date"])
        xs_r, non_r, all_r = [], [], []
        xs_codes, non_codes = [], []
        for c in codes:
            ret = stock_ret(c, bd, sd)
            if ret is None: continue
            all_r.append(ret)
            if c in xishen:
                xs_r.append(ret); xs_codes.append(c)
            else:
                non_r.append(ret); non_codes.append(c)
        if not all_r: continue
        xs_rets_all += xs_r; non_rets_all += non_r

        # 组合净值(等权，扣换手成本)
        g_all = np.mean(all_r)
        nav_all *= (1 + g_all - cost(codes, prev_all)); prev_all = codes
        curve_all.append(nav_all)
        if xs_r:
            g_xs = np.mean(xs_r)
            nav_xs *= (1 + g_xs - cost(xs_codes, prev_xs)); prev_xs = xs_codes
        curve_xs.append(nav_xs)
        if non_r:
            g_non = np.mean(non_r)
            nav_non *= (1 + g_non - cost(non_codes, prev_non)); prev_non = non_codes
        curve_non.append(nav_non)

        period_rows.append({
            "date": r["rebalance_date"],
            "n_xs": len(xs_r), "n_non": len(non_r),
            "xs_mean": np.mean(xs_r) if xs_r else np.nan,
            "non_mean": np.mean(non_r) if non_r else np.nan,
        })

    pr = pd.DataFrame(period_rows)
    xs_arr = np.array(xs_rets_all); non_arr = np.array(non_rets_all)

    def perf(curve):
        a = np.array(curve); n = len(a)-1; yrs = n/12
        ann = a[-1]**(1/yrs)-1 if yrs>0 and a[-1]>0 else 0
        peak = np.maximum.accumulate(a); mdd = ((a-peak)/peak).min()
        return (a[-1]-1)*100, ann*100, mdd*100

    print("\n" + "="*72)
    print("S012 快速验证 · 喜神(金水) vs 非喜神 —— 在S009的113期选股结果内")
    print("="*72)

    print(f"\n【单票收益池】(不分期，所有被选中的个股)")
    print(f"  喜神票  : {len(xs_arr):>5}只次  平均{xs_arr.mean()*100:+.2f}%  "
          f"中位{np.median(xs_arr)*100:+.2f}%  胜率{(xs_arr>0).mean()*100:.1f}%")
    print(f"  非喜神票: {len(non_arr):>5}只次  平均{non_arr.mean()*100:+.2f}%  "
          f"中位{np.median(non_arr)*100:+.2f}%  胜率{(non_arr>0).mean()*100:.1f}%")
    diff = (xs_arr.mean()-non_arr.mean())*100
    print(f"  → 喜神票平均单票收益 {'高' if diff>0 else '低'} {abs(diff):.2f}个百分点")

    # 占比
    tot = len(xs_arr)+len(non_arr)
    print(f"\n  喜神票在S009选股中占比: {len(xs_arr)/tot*100:.1f}% "
          f"(全市场喜神占比约 {1485/5201*100:.1f}%)")

    print(f"\n【组合净值】(等权+扣换手成本，113期复利)")
    tA,aA,dA = perf(curve_all)
    tX,aX,dX = perf(curve_xs)
    tN,aN,dN = perf(curve_non)
    print(f"  S009原版(全选) : 累计{tA:+.0f}%  年化{aA:+.1f}%  回撤{dA:.1f}%")
    print(f"  只买喜神票      : 累计{tX:+.0f}%  年化{aX:+.1f}%  回撤{dX:.1f}%")
    print(f"  只买非喜神票    : 累计{tN:+.0f}%  年化{aN:+.1f}%  回撤{dN:.1f}%")

    # 逐期对比：喜神跑赢非喜神的期数
    valid = pr.dropna(subset=["xs_mean","non_mean"])
    xs_win = (valid["xs_mean"] > valid["non_mean"]).sum()
    print(f"\n【逐期对比】(两组都有票的{len(valid)}期)")
    print(f"  喜神组跑赢非喜神组: {xs_win}/{len(valid)}期 ({xs_win/len(valid)*100:.0f}%)")

    print(f"\n【判读】")
    if aX > aA and diff > 0:
        print(f"  ✓ 喜神池收窄后年化更高({aX:.1f}% vs {aA:.1f}%)，值得开S013完整重训验证")
    elif aX < aA - 3:
        print(f"  ✗ 只买喜神明显跑输全选({aX:.1f}% vs {aA:.1f}%)，金水池alpha密度更低，慎开S013")
    else:
        print(f"  △ 喜神与全选接近({aX:.1f}% vs {aA:.1f}%)，收窄没明显增益也没大损失")

    import json
    json.dump({
        "xishen_pool_size": len(xishen),
        "single_stock": {
            "xishen": {"n": len(xs_arr), "mean_pct": round(xs_arr.mean()*100,3),
                       "median_pct": round(float(np.median(xs_arr))*100,3),
                       "win_pct": round((xs_arr>0).mean()*100,1)},
            "non": {"n": len(non_arr), "mean_pct": round(non_arr.mean()*100,3),
                    "median_pct": round(float(np.median(non_arr))*100,3),
                    "win_pct": round((non_arr>0).mean()*100,1)},
            "diff_pct": round(diff,3),
            "xishen_share_in_s009": round(len(xs_arr)/tot*100,1),
        },
        "portfolio": {
            "all":    {"total_pct": round(tA,1), "annual_pct": round(aA,1), "mdd_pct": round(dA,1)},
            "xishen": {"total_pct": round(tX,1), "annual_pct": round(aX,1), "mdd_pct": round(dX,1)},
            "non":    {"total_pct": round(tN,1), "annual_pct": round(aN,1), "mdd_pct": round(dN,1)},
        },
        "period_xs_winrate": round(xs_win/len(valid)*100,1),
        "curves": {"all": [round(x,4) for x in curve_all],
                   "xishen": [round(x,4) for x in curve_xs],
                   "non": [round(x,4) for x in curve_non]},
        "dates": ["起点"] + t["rebalance_date"].astype(str).tolist(),
    }, open("/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-18-S012-五行月份LightGBM选股/xishen_quickcheck_result.json","w"),
       ensure_ascii=False, indent=2, default=str)
    log(f"结果已保存 xishen_quickcheck_result.json  耗时{time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
