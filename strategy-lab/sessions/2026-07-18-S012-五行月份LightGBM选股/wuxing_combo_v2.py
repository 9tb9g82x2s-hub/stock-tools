#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S012 · 月份五行×个股五行联合择股 方案A修正版（老大亲自拍板的命理规则）

老大命盘：癸水日主身弱，喜金水忌木火土
老大拍板三点：
  1. 木月只选金（不选水：水生木会助忌神）
  2. 土月选金（金泄土：土生金疏导旺土）
  3. 金月金就够（金月已旺，金+水也可）

修正后的方案A规则（vs之前"木土月都选金水"的错误版）：
  木月 → 金        （金生水补泄，不含水防水生木）
  火月 → 水        （水克火制忌）
  土月 → 金        （金泄土，土生金疏导）
  金月 → 金 or 金水  （跑两个子版本对比）
  水月 → 水        （同气助身）

方案B（纯五行相生，对照）：不变
  木月→木水  火月→火木  土月→土火  金月→金土  水月→水金
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


# ---- 方案定义 ----
# A修正版：老大亲自拍板
PLAN_A2 = {"木": {"金"}, "火": {"水"}, "土": {"金"}, "金": {"金"}, "水": {"水"}}
# A修正版-宽松：金月允许金+水
PLAN_A2b = {"木": {"金"}, "火": {"水"}, "土": {"金"}, "金": {"金","水"}, "水": {"水"}}
# B纯五行相生（对照）
PLAN_B  = {"木": {"木","水"}, "火": {"火","木"}, "土": {"土","火"}, "金": {"金","土"}, "水": {"水","金"}}
# A旧版（之前跑的，留作对照）
PLAN_A_OLD = {"木": {"金","水"}, "火": {"水"}, "土": {"金","水"}, "金": {"金","水"}, "水": {"水"}}


def main():
    t0 = time.time()
    wx = pd.read_excel("/Users/ziruzhu/Downloads/A股五行属性标注_紫儒版.xlsx",
                       sheet_name="A股五行标注(全量)", header=1)
    wx.columns = ['代码','名称','板块','主五行','辅五行','对紫儒','建议','评分明细','分类依据']
    wx['代码'] = wx['代码'].astype(str).str.zfill(6)
    def to_ts(c):
        if c.startswith(('60','68','9','11')): return c+'.SH'
        if c.startswith(('00','30','12','20')): return c+'.SZ'
        return c+'.BJ'
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
        nav=1.0; curve=[1.0]; prev=set(); n_empty=0; n_pick_total=0; picked_p=0
        for _, r in t.iterrows():
            codes = ast.literal_eval(r["holdings"])
            bd, sd = str(r["buy_date"]), str(r["sell_date"])
            allow = plan.get(r["m_wx"], set())
            picks_raw = [c for c in codes if stock_wx.get(c) in allow]
            rr = [sret(c,bd,sd) for c in picks_raw]
            valid = [(c,x) for c,x in zip(picks_raw,rr) if x is not None]
            if valid:
                vc = [c for c,_ in valid]; vr = [x for _,x in valid]
                nav *= (1 + np.mean(vr) - cost(vc, prev)); prev = set(vc)
                n_pick_total += len(vr); picked_p += 1
            else:
                nav *= (1 + CASH_M); prev = set(); n_empty += 1
            curve.append(nav)
        arr=np.array(curve); n=len(arr)-1; yrs=n/12
        ann=arr[-1]**(1/yrs)-1 if yrs>0 and arr[-1]>0 else 0
        peak=np.maximum.accumulate(arr); mdd=((arr-peak)/peak).min()
        avg_pick = n_pick_total/picked_p if picked_p else 0
        log(f"{name}: 累计{(arr[-1]-1)*100:+.0f}%  年化{ann*100:+.1f}%  回撤{mdd*100:.1f}%  "
            f"均持{avg_pick:.1f}只  空仓{n_empty}期")
        return {"total_pct":round((arr[-1]-1)*100,1),"annual_pct":round(ann*100,1),
                "mdd_pct":round(mdd*100,1),"avg_picks":round(avg_pick,1),
                "empty_periods":n_empty,
                "curve":[round(float(x),4) for x in curve]}

    def run_base():
        nav=1.0; curve=[1.0]; prev=set()
        for _, r in t.iterrows():
            codes=ast.literal_eval(r["holdings"]); bd,sd=str(r["buy_date"]),str(r["sell_date"])
            rr=[sret(c,bd,sd) for c in codes]; vv=[c for c,x in zip(codes,rr) if x is not None]
            rr=[x for x in rr if x is not None]
            if rr: nav*=(1+np.mean(rr)-cost(vv,prev)); prev=set(vv)
            curve.append(nav)
        arr=np.array(curve); n=len(arr)-1; yrs=n/12
        ann=arr[-1]**(1/yrs)-1 if yrs>0 else 0
        peak=np.maximum.accumulate(arr); mdd=((arr-peak)/peak).min()
        log(f"基线(S009全选): 累计{(arr[-1]-1)*100:+.0f}%  年化{ann*100:+.1f}%  回撤{mdd*100:.1f}%  均持20只")
        return {"total_pct":round((arr[-1]-1)*100,1),"annual_pct":round(ann*100,1),
                "mdd_pct":round(mdd*100,1),"curve":[round(float(x),4) for x in curve]}

    print("\n"+"="*76)
    print("S012 · 月份五行×个股五行 方案A修正版（老大亲拍命理规则）")
    print("="*76)
    print("规则：木月→金  火月→水  土月→金  金月→金  水月→水")
    print("      （木月不含水：防水生木；土月不含水：防土克水）\n")

    base   = run_base()
    rA_old = run_plan(PLAN_A_OLD, "A旧版(木土月含金水)")
    rA2    = run_plan(PLAN_A2,    "A修正(严格:金月仅金)")
    rA2b   = run_plan(PLAN_A2b,   "A修正宽松(金月金+水)")
    rB     = run_plan(PLAN_B,     "B纯五行相生(对照)  ")

    print(f"\n{'方案':<28}{'累计%':>8}{'年化%':>7}{'回撤%':>7}{'均持':>5}{'空仓':>5}{'年化Δ':>8}")
    print("-"*70)
    for nm, r, d in [
        ("基线 S009全选",       base,   None),
        ("A旧版(木土月含金水)", rA_old, rA_old['annual_pct']-base['annual_pct']),
        ("A修正·严格",          rA2,    rA2['annual_pct']-base['annual_pct']),
        ("A修正·宽松(金月+水)", rA2b,   rA2b['annual_pct']-base['annual_pct']),
        ("B纯五行相生",         rB,     rB['annual_pct']-base['annual_pct']),
    ]:
        avg = r.get('avg_picks',20); emp = r.get('empty_periods',0)
        dstr = f"{d:>+8.1f}" if d is not None else f"{'—':>8}"
        print(f"{nm:<28}{r['total_pct']:>8.0f}{r['annual_pct']:>7.1f}{r['mdd_pct']:>7.1f}"
              f"{avg:>5.1f}{emp:>5}{dstr}")

    print(f"\n【判读】")
    best = max([("A修正严格",rA2),("A修正宽松",rA2b),("B",rB)], key=lambda x:x[1]['annual_pct'])
    bA = max(rA2['annual_pct'], rA2b['annual_pct'])
    if bA > base['annual_pct']:
        print(f"  ✓ 方案A修正版年化{bA:.1f}%跑赢基线，命理规则有效，可考虑开S013")
    else:
        diff2 = rA2['annual_pct'] - rA_old['annual_pct']
        print(f"  方案A修正({bA:.1f}%) vs 旧版({rA_old['annual_pct']:.1f}%)，"
              f"{'修正后有提升+' if diff2>0 else '修正后略降'}{abs(diff2):.1f}pt")
        print(f"  两版本均跑输基线，金水行业alpha低是根本原因，命理规则精细化帮助有限")
    if best[1]['annual_pct'] > base['annual_pct']:
        print(f"  整体最优: {best[0]}  年化{best[1]['annual_pct']:.1f}%")

    # 逐月五行统计：看各月份五行期数 & 命中率
    print(f"\n【月份五行分布 & 命中情况（113期）】")
    print(f"{'月份五行':<8}{'期数':>5}  A修正允许  持仓票数均值")
    for mw in ["木","火","土","金","水"]:
        sub = t[t["m_wx"]==mw]
        n = len(sub)
        allow = PLAN_A2[mw]
        print(f"  {mw}月{'':<5}{n:>4}期  → {'/'.join(sorted(allow))}股")

    json.dump({"plan_A2_def":{k:list(v) for k,v in PLAN_A2.items()},
               "plan_A2b_def":{k:list(v) for k,v in PLAN_A2b.items()},
               "plan_B_def":{k:list(v) for k,v in PLAN_B.items()},
               "base":{k:v for k,v in base.items() if k!="curve"},
               "A_old":{k:v for k,v in rA_old.items() if k!="curve"},
               "A2":{k:v for k,v in rA2.items() if k!="curve"},
               "A2b":{k:v for k,v in rA2b.items() if k!="curve"},
               "B":{k:v for k,v in rB.items() if k!="curve"},
               "curves":{"base":base["curve"],"A_old":rA_old["curve"],
                         "A2":rA2["curve"],"A2b":rA2b["curve"],"B":rB["curve"]},
               "dates":["起点"]+t["rebalance_date"].astype(str).tolist()},
              open(f"{S012}/wuxing_combo_v2_result.json","w"), ensure_ascii=False, indent=2, default=str)
    log(f"结果已存 wuxing_combo_v2_result.json  耗时{time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
