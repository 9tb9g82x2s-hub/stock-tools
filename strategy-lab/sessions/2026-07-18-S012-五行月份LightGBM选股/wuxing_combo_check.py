#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S012 快速验证 · 月份五行 × 个股五行 联合择股（方案A命主为中心 + 方案B纯五行）

思路：不重训。拆S009的113期持仓，给每只票打个股五行标签(全量表)，
再按"当期买入日的月份五行"决定该期允许买入的个股五行集合，
只保留符合条件的票等权持有；某期无符合票则空仓持现金(年化3%)。

方案A（命主癸水身弱喜金水，用个股五行扶用命主）:
  木月→金水  火月→水  土月→金水  金月→金水  水月→水
方案B（纯五行母子相生，与命主无关）:
  木月→木水  火月→火木  土月→土火  金月→金土  水月→水金

局限：S009的Top20是全市场选的，五行过滤后每期样本变稀疏，这是快速验证非重训版。
"""
import pandas as pd, numpy as np, sqlite3, ast, time, json

BASE = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股"
S012 = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-18-S012-五行月份LightGBM选股"
DB = "/Users/ziruzhu/stock-data/stock_all.db"
CASH_M = (1.03) ** (1/12) - 1
BUY_COMM, SELL_COMM, STAMP = 0.00025, 0.00025, 0.0005


def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def month_wuxing(date_str):
    d = str(date_str); md = int(d[4:6])*100 + int(d[6:8])
    if   204 <= md <= 404: return "木"
    elif 405 <= md <= 505: return "土"
    elif 506 <= md <= 706: return "火"
    elif 707 <= md <= 807: return "土"
    elif 808 <= md <= 1007: return "金"
    elif 1008 <= md <= 1107: return "土"
    elif md >= 1108 or md <= 105: return "水"
    elif 106 <= md <= 203: return "土"
    return "?"


# 月份五行 → 允许的个股五行集合
PLAN_A = {"木": {"金","水"}, "火": {"水"}, "土": {"金","水"}, "金": {"金","水"}, "水": {"水"}}
PLAN_B = {"木": {"木","水"}, "火": {"火","木"}, "土": {"土","火"}, "金": {"金","土"}, "水": {"水","金"}}


def main():
    t0 = time.time()
    # 个股五行映射（全量表）
    wx = pd.read_excel(f"/Users/ziruzhu/Downloads/A股五行属性标注_紫儒版.xlsx",
                       sheet_name="A股五行标注(全量)", header=1)
    wx.columns = ['代码','名称','板块','主五行','辅五行','对紫儒','建议','评分明细','分类依据']
    wx['代码'] = wx['代码'].astype(str).str.zfill(6)
    def to_ts(c):
        if c.startswith(('60','68','9','11')): return c+'.SH'
        if c.startswith(('00','30','12','20')): return c+'.SZ'
        if c.startswith(('43','83','87','8')): return c+'.BJ'
        return c+'.SZ'
    wx['ts_code'] = wx['代码'].apply(to_ts)
    stock_wx = dict(zip(wx['ts_code'], wx['主五行']))
    log(f"个股五行映射: {len(stock_wx)} 只")

    t = pd.read_csv(f"{BASE}/trades_full.csv").sort_values("rebalance_date").reset_index(drop=True)
    t["m_wx"] = t["buy_date"].apply(month_wuxing)

    all_codes = set()
    for _, r in t.iterrows(): all_codes.update(ast.literal_eval(r["holdings"]))
    min_d, max_d = str(t["buy_date"].min()), str(t["sell_date"].max())
    con = sqlite3.connect(DB)
    cs = ",".join(f"'{c}'" for c in all_codes)
    px = pd.read_sql(f"SELECT ts_code, trade_date, open_qfq FROM stk_factor "
                     f"WHERE trade_date BETWEEN '{min_d}' AND '{max_d}' AND ts_code IN ({cs})", con)
    con.close()
    px["open_qfq"] = pd.to_numeric(px["open_qfq"], errors="coerce")
    open_lk = px.set_index(["ts_code","trade_date"])["open_qfq"]
    log(f"价格加载 {len(px):,} 行")

    def sret(c, bd, sd):
        try:
            p0, p1 = open_lk.loc[(c,bd)], open_lk.loc[(c,sd)]
            if pd.notna(p0) and pd.notna(p1) and p0>0: return float(p1)/float(p0)-1
        except KeyError: return None
        return None

    def cost(curr, prev):
        c,p=set(curr),set(prev); nc=len(c) or 1; npv=len(p) or 1
        return len(c-p)/nc*BUY_COMM + len(p-c)/npv*(SELL_COMM+STAMP)

    def run_plan(plan, name):
        nav=1.0; curve=[1.0]; prev=set(); n_empty=0; n_pick_total=0; picked_periods=0
        rets_period=[]
        for _, r in t.iterrows():
            codes = ast.literal_eval(r["holdings"])
            bd, sd = str(r["buy_date"]), str(r["sell_date"])
            allow = plan.get(r["m_wx"], set())
            picks = [c for c in codes if stock_wx.get(c) in allow]
            rr = [sret(c,bd,sd) for c in picks]
            rr = [x for x in rr if x is not None]
            picks_valid = [c for c,x in zip(picks,[sret(c,bd,sd) for c in picks]) if x is not None]
            if rr:
                g = np.mean(rr)
                nav *= (1 + g - cost(picks_valid, prev)); prev = set(picks_valid)
                rets_period.append(g); n_pick_total += len(rr); picked_periods += 1
            else:
                nav *= (1 + CASH_M); prev = set(); n_empty += 1
                rets_period.append(CASH_M)
            curve.append(nav)
        arr=np.array(curve); n=len(arr)-1; yrs=n/12
        ann=arr[-1]**(1/yrs)-1 if yrs>0 and arr[-1]>0 else 0
        peak=np.maximum.accumulate(arr); mdd=((arr-peak)/peak).min()
        avg_pick = n_pick_total/picked_periods if picked_periods else 0
        log(f"{name}: 累计{(arr[-1]-1)*100:+.0f}% 年化{ann*100:+.1f}% 回撤{mdd*100:.1f}% "
            f"| 有票{picked_periods}期(均{avg_pick:.1f}只) 空仓{n_empty}期")
        return {"total_pct":round((arr[-1]-1)*100,1),"annual_pct":round(ann*100,1),
                "mdd_pct":round(mdd*100,1),"picked_periods":picked_periods,
                "empty_periods":n_empty,"avg_picks":round(avg_pick,1),
                "curve":[round(float(x),4) for x in curve]}

    # 基线：S009全选（每期20只全买，不过滤）
    def run_base():
        nav=1.0; curve=[1.0]; prev=set()
        for _, r in t.iterrows():
            codes=ast.literal_eval(r["holdings"]); bd,sd=str(r["buy_date"]),str(r["sell_date"])
            rr=[sret(c,bd,sd) for c in codes]; vv=[c for c,x in zip(codes,rr) if x is not None]
            rr=[x for x in rr if x is not None]
            if rr:
                nav*=(1+np.mean(rr)-cost(vv,prev)); prev=set(vv)
            curve.append(nav)
        arr=np.array(curve); n=len(arr)-1; yrs=n/12
        ann=arr[-1]**(1/yrs)-1 if yrs>0 else 0
        peak=np.maximum.accumulate(arr); mdd=((arr-peak)/peak).min()
        log(f"基线(S009全选): 累计{(arr[-1]-1)*100:+.0f}% 年化{ann*100:+.1f}% 回撤{mdd*100:.1f}%")
        return {"total_pct":round((arr[-1]-1)*100,1),"annual_pct":round(ann*100,1),
                "mdd_pct":round(mdd*100,1),"curve":[round(float(x),4) for x in curve]}

    print("\n"+"="*74)
    print("S012 · 月份五行 × 个股五行 联合择股 快速验证")
    print("="*74)
    base = run_base()
    resA = run_plan(PLAN_A, "方案A(命主为中心)")
    resB = run_plan(PLAN_B, "方案B(纯五行相生)")

    print(f"\n{'方案':<22}{'累计%':>9}{'年化%':>8}{'回撤%':>8}{'年化Δ':>8}")
    print("-"*58)
    print(f"{'基线 S009全选':<24}{base['total_pct']:>9.0f}{base['annual_pct']:>8.1f}{base['mdd_pct']:>8.1f}{'—':>8}")
    for nm,r in [("方案A 命主为中心",resA),("方案B 纯五行相生",resB)]:
        d=r['annual_pct']-base['annual_pct']
        print(f"{nm:<24}{r['total_pct']:>9.0f}{r['annual_pct']:>8.1f}{r['mdd_pct']:>8.1f}{d:>+8.1f}")

    print(f"\n【判读】")
    best = max([("A",resA['annual_pct']),("B",resB['annual_pct'])], key=lambda x:x[1])
    if resA['annual_pct']>base['annual_pct'] or resB['annual_pct']>base['annual_pct']:
        print(f"  方案{best[0]}年化{best[1]:.1f}%跑赢基线，联合择股或有价值，可考虑开S013重训")
    else:
        print(f"  两方案均跑输基线(A{resA['annual_pct']:.1f}%/B{resB['annual_pct']:.1f}% vs 基线{base['annual_pct']:.1f}%)")
        print(f"  月份五行×个股五行的双重过滤把S009的选股alpha切碎了，不建议开S013")
    print(f"  注：五行过滤后每期样本大幅缩水(A均{resA['avg_picks']}只/B均{resB['avg_picks']}只)，")
    print(f"      空仓期A={resA['empty_periods']}/B={resB['empty_periods']}，统计噪音较大，仅供方向参考")

    json.dump({"plan_A_def":{k:list(v) for k,v in PLAN_A.items()},
               "plan_B_def":{k:list(v) for k,v in PLAN_B.items()},
               "base":{k:v for k,v in base.items() if k!="curve"},
               "planA":{k:v for k,v in resA.items() if k!="curve"},
               "planB":{k:v for k,v in resB.items() if k!="curve"},
               "curves":{"base":base["curve"],"A":resA["curve"],"B":resB["curve"]},
               "dates":["起点"]+t["rebalance_date"].astype(str).tolist()},
              open(f"{S012}/wuxing_combo_result.json","w"), ensure_ascii=False, indent=2, default=str)
    log(f"结果已存 wuxing_combo_result.json 耗时{time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
