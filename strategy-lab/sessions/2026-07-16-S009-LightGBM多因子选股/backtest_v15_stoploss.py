#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
S009 v1.5 · 个股止损版回测

思路：复用已有的每期选股结果（trades_full.csv 里的 holdings + buy_date + sell_date），
不重新训练模型，只在收益计算环节加入"单只股票止损"逻辑，做"加止损 vs 不加止损"的
干净对照。

止损规则：
- 买入价 = 买入日(buy_date)的前复权开盘价 open_qfq
- 止损线 = 买入价 × (1 - STOP_PCT)，默认 STOP_PCT=0.10（跌10%止损）
- 持仓期间(buy_date < d <= sell_date)每个交易日，检查当日前复权收盘价 close_qfq：
  第一个 close_qfq <= 止损线 的交易日 → 以该日 close_qfq 出场（保守用收盘价，可执行）
  出场后该笔剩余期间持现金（收益锁定在止损价）
- 未触发止损的 → sell_date 的 open_qfq 正常卖出（与原版口径一致）
- 交易成本：买入佣金；卖出（无论正常卖还是止损卖）佣金+印花税

无未来函数：止损用的是持仓期间"当天能看到"的收盘价，实盘可执行。
"""
import json
import sqlite3
import time
import ast
import numpy as np
import pandas as pd

BASE_DIR = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股"
DB_PATH = "/Users/ziruzhu/stock-data/stock_all.db"
STOP_PCT = 0.10  # 跌10%止损

BUY_COMMISSION = 0.00025
SELL_COMMISSION = 0.00025
STAMP_TAX = 0.0005


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main():
    t0 = time.time()
    log("读 trades_full.csv (复用已有选股结果)...")
    t = pd.read_csv(f"{BASE_DIR}/trades_full.csv")
    log(f"调仓期数: {len(t)}")

    # 收集所有需要的股票代码和日期范围
    all_codes = set()
    for _, r in t.iterrows():
        codes = ast.literal_eval(r["holdings"])
        all_codes.update(codes)
    log(f"涉及股票数: {len(all_codes)}")

    min_date = str(t["buy_date"].min())
    max_date = str(t["sell_date"].max())
    log(f"日期范围: {min_date} ~ {max_date}")

    # 预加载全部相关股票的前复权日线到内存
    log("预加载 stk_factor 前复权日线...")
    con = sqlite3.connect(DB_PATH)
    codes_str = ",".join([f"'{c}'" for c in all_codes])
    px = pd.read_sql(
        f"SELECT ts_code, trade_date, open_qfq, close_qfq FROM stk_factor "
        f"WHERE trade_date BETWEEN '{min_date}' AND '{max_date}' "
        f"AND ts_code IN ({codes_str})", con)
    con.close()
    px["open_qfq"] = pd.to_numeric(px["open_qfq"], errors="coerce")
    px["close_qfq"] = pd.to_numeric(px["close_qfq"], errors="coerce")
    log(f"日线记录: {len(px):,} 行")

    # 建立快速查询结构：每只股票一个按日期排序的 DataFrame
    px = px.sort_values(["ts_code", "trade_date"])
    open_lookup = px.set_index(["ts_code", "trade_date"])["open_qfq"]
    # 每只股票的 (日期数组, 收盘价数组)，用于持仓期间遍历
    close_by_code = {}
    for code, sub in px.groupby("ts_code"):
        close_by_code[code] = (sub["trade_date"].values, sub["close_qfq"].values)

    all_trade_dates = sorted(px["trade_date"].unique())

    def simulate_holding(code, buy_date, sell_date):
        """返回该笔持仓收益（含止损逻辑），止损触发返回(ret, True)"""
        try:
            p_buy = open_lookup.loc[(code, buy_date)]
        except KeyError:
            return None, False
        if pd.isna(p_buy) or p_buy <= 0:
            return None, False
        stop_price = p_buy * (1 - STOP_PCT)

        # 遍历持仓期间(buy_date < d <= sell_date)每日收盘价，找首次跌破止损线
        if code in close_by_code:
            dates, closes = close_by_code[code]
            # 只看 buy_date 之后、sell_date 及之前的交易日
            mask = (dates > buy_date) & (dates <= sell_date)
            for d, c in zip(dates[mask], closes[mask]):
                if pd.notna(c) and c <= stop_price:
                    # 止损出场，用该日收盘价（保守，实盘可当日尾盘卖）
                    return float(c) / float(p_buy) - 1, True

        # 未触发止损，正常 sell_date 开盘价卖出
        try:
            p_sell = open_lookup.loc[(code, sell_date)]
            if pd.isna(p_sell) or p_sell <= 0:
                return None, False
            return float(p_sell) / float(p_buy) - 1, False
        except KeyError:
            return None, False

    # ---- 逐期回测（加止损） ----
    nav_sl = 1.0
    nav_curve_sl = []
    period_rets_sl = []
    stop_triggered_total = 0
    prev_holdings = set()

    # 同时算无止损基线（用同样的持仓，确保对照干净）
    nav_base = 1.0
    period_rets_base = []
    nav_curve_base = []

    for _, r in t.iterrows():
        codes = ast.literal_eval(r["holdings"])
        buy_date = str(r["buy_date"])
        sell_date = str(r["sell_date"])

        rets_sl, rets_base = [], []
        valid_holdings = []
        n_stopped = 0
        for code in codes:
            ret_sl, stopped = simulate_holding(code, buy_date, sell_date)
            if ret_sl is None:
                continue
            rets_sl.append(ret_sl)
            valid_holdings.append(code)
            if stopped:
                n_stopped += 1
            # 无止损基线：同一笔，纯 buy->sell
            try:
                p0 = open_lookup.loc[(code, buy_date)]
                p1 = open_lookup.loc[(code, sell_date)]
                if pd.notna(p0) and pd.notna(p1) and p0 > 0:
                    rets_base.append(float(p1) / float(p0) - 1)
            except KeyError:
                pass

        if len(rets_sl) == 0:
            continue

        stop_triggered_total += n_stopped
        gross_sl = float(np.mean(rets_sl))
        gross_base = float(np.mean(rets_base)) if rets_base else gross_sl

        # 交易成本（止损版换手更高：止损卖出的票下期不在持仓，等价于换手）
        curr = set(valid_holdings)
        bought = curr - prev_holdings
        sold = prev_holdings - curr
        n_curr = len(curr) if curr else 1
        n_prev = len(prev_holdings) if prev_holdings else 1
        buy_to = len(bought) / n_curr if n_curr else 0
        sell_to = len(sold) / n_prev if n_prev else 0
        # 止损额外卖出成本：本期止损的票也要付卖出成本
        extra_stop_cost = (n_stopped / n_curr) * (SELL_COMMISSION + STAMP_TAX) if n_curr else 0
        cost_sl = buy_to * BUY_COMMISSION + sell_to * (SELL_COMMISSION + STAMP_TAX) + extra_stop_cost
        cost_base = buy_to * BUY_COMMISSION + sell_to * (SELL_COMMISSION + STAMP_TAX)

        period_ret_sl = gross_sl - cost_sl
        period_ret_base = gross_base - cost_base

        nav_sl *= (1 + period_ret_sl)
        nav_base *= (1 + period_ret_base)
        period_rets_sl.append(period_ret_sl)
        period_rets_base.append(period_ret_base)
        nav_curve_sl.append({"date": sell_date, "nav": round(nav_sl, 6), "n_stop": n_stopped})
        nav_curve_base.append({"date": sell_date, "nav": round(nav_base, 6)})
        prev_holdings = curr

    def metrics(period_rets, nav_curve, nav):
        pr = np.array(period_rets)
        n = len(pr)
        n_years = n / 12.0 if n else 1
        ann = (nav ** (1 / n_years) - 1) if n_years > 0 and nav > 0 else 0
        nav_series = np.array([1.0] + [c["nav"] for c in nav_curve])
        rmax = np.maximum.accumulate(nav_series)
        dd = float((nav_series / rmax - 1).min())
        sharpe = float(pr.mean() / pr.std() * np.sqrt(12)) if n > 1 and pr.std() > 0 else 0
        win = float(np.mean(pr > 0)) if n else 0
        return {"total_return": round(float(nav - 1), 4), "annual_return": round(ann, 4),
                "max_drawdown": round(dd, 4), "sharpe": round(sharpe, 4),
                "win_rate": round(win, 4), "n_periods": n}

    m_sl = metrics(period_rets_sl, nav_curve_sl, nav_sl)
    m_base = metrics(period_rets_base, nav_curve_base, nav_base)

    # 年度分解
    t2 = t.copy()
    t2["year"] = t2["rebalance_date"].astype(str).str[:4]
    yearly = []
    idx = 0
    year_nav_sl = {}
    year_nav_base = {}
    year_stops = {}
    for i, (_, r) in enumerate(t.iterrows()):
        if i >= len(period_rets_sl):
            break
        yr = str(r["rebalance_date"])[:4]
        year_nav_sl.setdefault(yr, []).append(period_rets_sl[i])
        year_nav_base.setdefault(yr, []).append(period_rets_base[i])
        year_stops[yr] = year_stops.get(yr, 0) + nav_curve_sl[i]["n_stop"]
    for yr in sorted(year_nav_sl.keys()):
        r_sl = float(np.prod([1 + x for x in year_nav_sl[yr]]) - 1) * 100
        r_base = float(np.prod([1 + x for x in year_nav_base[yr]]) - 1) * 100
        yearly.append({"year": yr, "sl_return": round(r_sl, 2),
                       "base_return": round(r_base, 2), "n_stops": year_stops[yr]})

    result = {
        "strategy": "S009 v1.5 个股10%止损",
        "stop_pct": STOP_PCT,
        "with_stoploss": m_sl,
        "no_stoploss_baseline": m_base,
        "total_stop_triggered": stop_triggered_total,
        "yearly": yearly,
        "nav_curve_sl": nav_curve_sl,
    }
    with open(f"{BASE_DIR}/v15_stoploss_result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    log("=" * 66)
    log(f"v1.5 止损版 vs 无止损基线（同一批持仓对照）")
    log(f"  无止损: 累计{m_base['total_return']*100:.1f}% 年化{m_base['annual_return']*100:.2f}% "
        f"回撤{m_base['max_drawdown']*100:.2f}% 夏普{m_base['sharpe']:.2f}")
    log(f"  加止损: 累计{m_sl['total_return']*100:.1f}% 年化{m_sl['annual_return']*100:.2f}% "
        f"回撤{m_sl['max_drawdown']*100:.2f}% 夏普{m_sl['sharpe']:.2f}")
    log(f"  10年累计止损触发: {stop_triggered_total} 笔")
    log("")
    log("逐年对比(无止损% / 加止损% / 止损笔数):")
    print(f"{'年份':<8}{'无止损':>10}{'加止损':>10}{'止损笔数':>10}")
    for y in yearly:
        print(f"{y['year']:<8}{y['base_return']:>+9.1f}%{y['sl_return']:>+9.1f}%{y['n_stops']:>10}")
    log(f"耗时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
