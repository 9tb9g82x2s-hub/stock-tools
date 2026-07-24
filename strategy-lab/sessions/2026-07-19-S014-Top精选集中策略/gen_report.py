"""生成 S014 Top精选集中策略 可视化HTML报告"""
import json, pandas as pd, numpy as np
from pathlib import Path

OUT = Path("/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S014-Top精选集中策略")
df009 = pd.read_csv(OUT / "s014_top_detail_s009.csv", encoding="utf-8-sig")
df013 = pd.read_csv(OUT / "s014_top_detail_s013b.csv", encoding="utf-8-sig")
stats = json.load(open(OUT / "s014_result.json"))

COLS = ["original_return", "top1_return", "top2_return", "top3_return"]
LABELS = {"original_return": "原策略Top20", "top1_return": "Top1", "top2_return": "Top2均权", "top3_return": "Top3均权"}

def nav_list(df, col):
    nav, out = 1.0, [1.0]
    for r in df[col]:
        if not pd.isna(r):
            nav *= (1 + r)
        out.append(round(nav, 4))
    return out

def yearly(df):
    df = df.copy()
    df["year"] = df["sell_date"].astype(str).str[:4]
    rows = {}
    for col in COLS:
        yr = {}
        for y, g in df.groupby("year"):
            s = g[col].dropna()
            yr[y] = round(float((1 + s).prod() - 1), 4) if len(s) else None
        rows[col] = yr
    return rows

nav009 = {c: nav_list(df009, c) for c in COLS}
nav013 = {c: nav_list(df013, c) for c in COLS}
labels009 = ["起始"] + df009["sell_date"].astype(str).tolist()
labels013 = ["起始"] + df013["sell_date"].astype(str).tolist()
yr009 = yearly(df009)
yr013 = yearly(df013)
years = sorted(set(list(yr009["top1_return"].keys()) + list(yr013["top1_return"].keys())))

payload = {
    "stats": stats, "nav009": nav009, "nav013": nav013,
    "labels009": labels009, "labels013": labels013,
    "yr009": yr009, "yr013": yr013, "years": years,
}
json.dump(payload, open(OUT / "report_data.json", "w"), ensure_ascii=False)
print("report_data.json 生成完成")
print("years:", years)
