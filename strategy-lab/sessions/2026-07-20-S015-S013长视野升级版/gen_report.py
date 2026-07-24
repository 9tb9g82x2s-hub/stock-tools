#!/usr/bin/env python3
"""生成 S015 策略HTML报告"""
import json
from pathlib import Path

OUT = Path("/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-20-S015-S013长视野升级版")

s015 = json.load(open(OUT / "s015_result.json"))
s009cv = json.load(open(OUT / "s009_h40_crossval.json"))
s013_orig = json.load(open("/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S013-喜神池LightGBM选股/s013b_result.json"))

# NAV 曲线
s015_nav  = [1.0] + [c["nav"] for c in s015["nav_curve"]]
s015_dates = ["起始"] + [c["date"] for c in s015["nav_curve"]]

# 把S013原版NAV降采样到57点（每2期取1）以便对比
s013_nav_full = [1.0] + [c["nav"] for c in s013_orig["nav_curve"]]
step = len(s013_nav_full) / len(s015_nav)
s013_nav_sampled = [s013_nav_full[int(i*step)] for i in range(len(s015_nav))]

# 逐年收益
import pandas as pd
def yearly(trades):
    df = pd.DataFrame(trades)
    df["year"] = df["sell_date"].astype(str).str[:4]
    yr = {}
    for y,g in df.groupby("year"):
        yr[y] = round(float((1+g["period_return"]).prod()-1)*100,1)
    return yr

yr15 = yearly(s015["trades"])
yr13 = yearly(s013_orig["trades"])
yr09cv = yearly(s009cv["trades"])
years = sorted(set(list(yr15.keys())+list(yr13.keys())))

payload = {
    "s015": s015["metrics"],
    "s013": s013_orig["metrics"],
    "s009cv": s009cv["metrics"],
    "s015_nav": s015_nav,
    "s013_nav": s013_nav_sampled,
    "s015_dates": s015_dates,
    "yr15": yr15, "yr13": yr13, "yr09cv": yr09cv,
    "years": years,
}

template = open(OUT / "report_template.html", encoding="utf-8").read()
import json as _json
html = template.replace("__PAYLOAD__", _json.dumps(payload, ensure_ascii=False))
(OUT / "s015_report.html").write_text(html, encoding="utf-8")
print("生成完成:", OUT / "s015_report.html")
