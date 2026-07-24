#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S019 实盘追踪看板 - 数据刷新脚本
读持仓配置 → 查数据库每日收盘价 → 算组合每日净值曲线+当前浮盈+持仓明细
输出 tracker_data.json 供 tracker.html 展示
每晚数据更新后运行（可幂等重复执行）
"""
import json, sqlite3, time
import pandas as pd
from pathlib import Path

BASE = Path("/Users/ziruzhu/stock-tools/strategy-lab/s019-tracker")
PORTFOLIO = BASE / "portfolio.json"
DB = "/Users/ziruzhu/stock-data/stock_all.db"
OUT = BASE / "tracker_data.json"

def log(m): print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)

def main():
    port = json.load(open(PORTFOLIO, encoding="utf-8"))
    positions = port["positions"]
    buy_date = port["buy_date"].replace("-", "")
    capital = port["capital"]
    codes = [p["code"] for p in positions]

    conn = sqlite3.connect(DB)
    cph = ",".join(f"'{c}'" for c in codes)
    px = pd.read_sql(
        f"SELECT ts_code, trade_date, close FROM daily "
        f"WHERE ts_code IN ({cph}) AND trade_date >= '{buy_date}' ORDER BY trade_date", conn)
    conn.close()
    px["close"] = pd.to_numeric(px["close"], errors="coerce")

    # 所有交易日（建仓日至今）
    trade_days = sorted(px["trade_date"].unique())
    if not trade_days:
        log("无价格数据，退出"); return
    log(f"建仓日 {buy_date}，数据覆盖 {trade_days[0]}~{trade_days[-1]}，共{len(trade_days)}天")

    # 每只票的价格透视表（日期×股票）
    pivot = px.pivot(index="trade_date", columns="ts_code", values="close")
    pivot = pivot.reindex(trade_days).ffill()  # 停牌用前值填充

    # 建仓成本（用建仓日价格 = portfolio里的buy_price）
    buy_price = {p["code"]: p["buy_price"] for p in positions}
    shares = {p["code"]: p["shares"] for p in positions}

    # 每日组合市值 → 净值曲线
    nav_curve = []
    total_cost = sum(buy_price[c]*shares[c] for c in codes)
    cash = capital - total_cost
    for d in trade_days:
        mv = sum((pivot.loc[d, c] if c in pivot.columns and pd.notna(pivot.loc[d, c]) else buy_price[c]) * shares[c] for c in codes)
        total_asset = mv + cash
        nav = total_asset / capital
        nav_curve.append({
            "date": f"{d[:4]}-{d[4:6]}-{d[6:]}",
            "nav": round(nav, 4),
            "market_value": round(mv, 0),
            "pnl": round(mv - total_cost, 0),
            "pnl_pct": round((mv/total_cost - 1)*100, 2)
        })

    # 当前持仓明细（最新日）
    last_d = trade_days[-1]
    holdings = []
    for p in positions:
        c = p["code"]
        now = float(pivot.loc[last_d, c]) if c in pivot.columns and pd.notna(pivot.loc[last_d, c]) else buy_price[c]
        buy = buy_price[c]; sh = shares[c]
        cost = buy*sh; mv = now*sh
        holdings.append({
            "code": c, "name": p["name"], "industry": p.get("industry",""),
            "buy_price": round(buy,2), "cur_price": round(now,2),
            "lots": p["lots"], "shares": sh,
            "cost": round(cost,0), "market_value": round(mv,0),
            "pnl": round(mv-cost,0), "pnl_pct": round((now/buy-1)*100,2),
            "stop_line": round(buy*0.88,2),
            "is_stop": now <= buy*0.88
        })
    holdings.sort(key=lambda x: -x["pnl_pct"])

    # 汇总
    cur = nav_curve[-1]
    summary = {
        "strategy": port.get("strategy","S019"),
        "buy_date": port["buy_date"],
        "sell_date_expected": port.get("sell_date_expected",""),
        "capital": capital,
        "total_cost": round(total_cost,0),
        "cash": round(cash,0),
        "cur_market_value": cur["market_value"],
        "cur_nav": cur["nav"],
        "cur_pnl": cur["pnl"],
        "cur_pnl_pct": cur["pnl_pct"],
        "days_held": len(trade_days),
        "latest_date": cur["date"],
        "n_stop": sum(1 for h in holdings if h["is_stop"]),
        "n_win": sum(1 for h in holdings if h["pnl"] > 0),
        "n_lose": sum(1 for h in holdings if h["pnl"] < 0),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }

    data = {"summary": summary, "nav_curve": nav_curve, "holdings": holdings}
    json.dump(data, open(OUT, "w"), ensure_ascii=False, indent=2)
    log(f"已更新: 持仓{len(holdings)}只, 净值{cur['nav']:.4f}, 浮盈{cur['pnl']:+.0f}元({cur['pnl_pct']:+.2f}%)")

    # 把数据内联进HTML模板，生成可直接打开的看板
    tpl_path = BASE / "tracker.html"
    if tpl_path.exists():
        tpl = tpl_path.read_text(encoding="utf-8")
        import re
        payload = json.dumps(data, ensure_ascii=False)
        # 替换 <script id="dataScript"> 里的内容
        new_html = re.sub(
            r'(<script id="dataScript" type="application/json">).*?(</script>)',
            lambda m: m.group(1) + payload + m.group(2),
            tpl, flags=re.DOTALL)
        (BASE / "index.html").write_text(new_html, encoding="utf-8")
        log(f"已生成看板: {BASE/'index.html'}")

if __name__ == "__main__":
    main()
