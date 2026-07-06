#!/usr/bin/python3
"""
离线股票分析脚本
从本地 SQLite 数据库读取数据，计算技术指标，生成分析报告

飞机上断网使用:
    python analyze.py 600105          # 分析永鼎股份
    python analyze.py 600105 --deep   # 深度分析（含长期指标）
    python analyze.py --scan          # 扫描全市场找出技术形态
    python analyze.py --top20         # 近20日涨幅前20

适配 LM Studio：输出可直接喂给 Qwen/DeepSeek 模型分析
"""

import sqlite3
import os
import sys
import json
import argparse
from collections import defaultdict

DB_PATH = os.path.expanduser("~/stock-data/all_stocks.db")
OUTPUT_DIR = os.path.expanduser("~/stock-data/reports")


def get_conn():
    """获取数据库连接"""
    if not os.path.exists(DB_PATH):
        print(f"[错误] 数据库不存在: {DB_PATH}")
        print("请先运行下载器: python download.py")
        sys.exit(1)
    return sqlite3.connect(DB_PATH)


def get_stock_data(conn, code, limit=None):
    """获取单只股票数据"""
    c = conn.cursor()
    if limit:
        c.execute(
            "SELECT * FROM stocks WHERE code=? ORDER BY date DESC LIMIT ?", 
            (code, limit)
        )
    else:
        c.execute("SELECT * FROM stocks WHERE code=? ORDER BY date", (code,))
    return c.fetchall()


def calc_ma(prices, period):
    """简单移动平均"""
    if len(prices) < period:
        return [None] * len(prices)
    result = [None] * (period - 1)
    for i in range(period - 1, len(prices)):
        result.append(sum(prices[i - period + 1:i + 1]) / period)
    return result


def calc_rsi(prices, period=14):
    """RSI指标"""
    if len(prices) < period + 1:
        return [None] * len(prices)
    
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    rsi = [None]
    
    gains = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]
    
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
        if avg_loss == 0:
            rsi.append(100)
        else:
            rs = avg_gain / avg_loss
            rsi.append(round(100 - 100 / (1 + rs), 2))
    
    return rsi


def calc_macd(prices, fast=12, slow=26, signal=9):
    """MACD指标"""
    if len(prices) < slow + signal:
        return [None] * len(prices), [None] * len(prices), [None] * len(prices)
    
    ema_fast = ema(prices, fast)
    ema_slow = ema(prices, slow)
    
    dif = [e_f - e_s if e_f is not None and e_s is not None else None 
           for e_f, e_s in zip(ema_fast, ema_slow)]
    
    # 对 DIF 中的有效值做 EMA 得到 DEA（跳过前面的 None）
    valid_dif = [d for d in dif if d is not None]
    if len(valid_dif) < signal:
        return dif, [None] * len(dif), [None] * len(dif)
    valid_dea = ema(valid_dif, signal)
    # 补齐前面的 None，保持长度一致
    none_count = len(dif) - len(valid_dif)
    dea = [None] * (none_count + signal - 1) + valid_dea[signal - 1:]
    
    # MACD 柱 = 2 * (DIF - DEA)
    macd_bar = [2 * (d - e) if d is not None and e is not None else None 
                for d, e in zip(dif, dea)]
    
    return dif, dea, macd_bar


def ema(data, period):
    """指数移动平均"""
    if len(data) < period:
        return [None] * len(data)
    
    result = [None] * (period - 1)
    k = 2 / (period + 1)
    
    # 第一个有效值为SMA
    result.append(sum(data[:period]) / period)
    
    for i in range(period, len(data)):
        if data[i] is not None:
            result.append(data[i] * k + result[-1] * (1 - k))
        else:
            result.append(result[-1])
    
    return result


def calc_kdj(highs, lows, closes, n=9):
    """KDJ指标"""
    if len(closes) < n:
        return [None] * len(closes), [None] * len(closes), [None] * len(closes)
    
    k_values, d_values, j_values = [], [], []
    prev_k, prev_d = 50, 50
    
    for i in range(len(closes)):
        if i < n - 1:
            k_values.append(None)
            d_values.append(None)
            j_values.append(None)
            continue
        
        highest = max(highs[i - n + 1:i + 1])
        lowest = min(lows[i - n + 1:i + 1])
        
        if highest == lowest:
            rsv = 50
        else:
            rsv = (closes[i] - lowest) / (highest - lowest) * 100
        
        k = prev_k * 2 / 3 + rsv / 3
        d = prev_d * 2 / 3 + k / 3
        j = 3 * k - 2 * d
        
        k_values.append(round(k, 2))
        d_values.append(round(d, 2))
        j_values.append(round(j, 2))
        
        prev_k, prev_d = k, d
    
    return k_values, d_values, j_values


def calc_obv(closes, volumes):
    """OBV 能量潮指标
    - 当日收盘 > 前日收盘: OBV = 前OBV + 当日成交量
    - 当日收盘 < 前日收盘: OBV = 前OBV - 当日成交量
    - 平盘: OBV 不变
    """
    if len(closes) < 2:
        return [0] * len(closes)
    obv = [0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv.append(obv[-1] + (volumes[i] or 0))
        elif closes[i] < closes[i - 1]:
            obv.append(obv[-1] - (volumes[i] or 0))
        else:
            obv.append(obv[-1])
    return obv


def obv_features(closes, volumes, window=20):
    """分析指定窗口内的 OBV 特征
    返回: {trend, slope, divergence, breakout, vol_expand}
    """
    n = len(closes)
    if n < window + 10:
        return None

    w_closes = closes[-window:]
    w_volumes = volumes[-window:]
    w_obv = calc_obv(w_closes, w_volumes)

    # 1. OBV 趋势（线性回归斜率）
    m = len(w_obv)
    x_mean = (m - 1) / 2
    y_mean = sum(w_obv) / m if m > 0 else 0
    denom = sum((i - x_mean) ** 2 for i in range(m))
    slope = sum((i - x_mean) * (w_obv[i] - y_mean) for i in range(m)) / denom if denom else 0
    # 相对于 OBV 均值的归一化斜率
    norm_slope = slope / (abs(y_mean) + 1) * 100 if abs(y_mean) > 0 else 0

    # 2. 价量背离（窗口前半 vs 后半）
    mid = m // 2
    p1 = sum(w_closes[:mid]) / mid
    p2 = sum(w_closes[mid:]) / (m - mid)
    o1 = sum(w_obv[:mid]) / mid
    o2 = sum(w_obv[mid:]) / (m - mid)
    price_chg = (p2 / p1 - 1) if p1 else 0
    obv_chg = (o2 / o1 - 1) if o1 else 0

    divergence = False
    div_type = "无"
    if price_chg < -0.03 and obv_chg > 0.03:
        divergence = True
        div_type = "价跌OBV升（吸筹信号）"
    elif price_chg > 0.03 and obv_chg < -0.03:
        divergence = True
        div_type = "价升OBV跌（出货信号）"

    # 3. OBV 突破（最近5天 OBV 是否创了前段新高）
    if m > 10:
        obv_breakout = max(w_obv[-5:]) > max(w_obv[:-5])
    else:
        obv_breakout = False

    # 4. 成交量放大（最近5天 vs 之前）
    if m > 10:
        vol_r = sum(w_volumes[-5:]) / 5
        vol_e = sum(w_volumes[:-5]) / (m - 5)
        vol_ratio = vol_r / vol_e if vol_e else 1
    else:
        vol_ratio = 1

    return {
        "obv_slope": round(norm_slope, 2),
        "obv_trend": "上升" if norm_slope > 0.8 else ("下降" if norm_slope < -0.8 else "横盘"),
        "divergence": divergence,
        "divergence_type": div_type,
        "obv_breakout": obv_breakout,
        "vol_expand_ratio": round(vol_ratio, 2),
        "vol_expand": vol_ratio > 1.5,
    }


def analyze_stock(conn, code, deep=False):
    """分析单只股票"""
    rows = get_stock_data(conn, code)
    if not rows:
        print(f"[错误] 数据库中没有股票 {code} 的数据")
        return None
    
    # 提取数据
    dates = [r[2] for r in rows]
    opens = [r[3] for r in rows]
    highs = [r[4] for r in rows]
    lows = [r[5] for r in rows]
    closes = [r[6] for r in rows]
    volumes = [r[7] for r in rows]
    amounts = [r[8] for r in rows]
    pct_changes = [r[10] for r in rows]
    turnovers = [r[11] for r in rows]
    
    name = rows[0][1] if rows else code
    
    # 计算技术指标
    ma5 = calc_ma(closes, 5)
    ma10 = calc_ma(closes, 10)
    ma20 = calc_ma(closes, 20)
    ma60 = calc_ma(closes, 60) if len(closes) >= 60 else [None] * len(closes)
    ma120 = calc_ma(closes, 120) if len(closes) >= 120 else [None] * len(closes)
    ma250 = calc_ma(closes, 250) if len(closes) >= 250 else [None] * len(closes)
    
    rsi6 = calc_rsi(closes, 6)
    rsi14 = calc_rsi(closes, 14)
    rsi24 = calc_rsi(closes, 24)
    
    dif, dea, macd_bar = calc_macd(closes)
    k_values, d_values, j_values = calc_kdj(highs, lows, closes)
    
    # 最新数据
    latest_idx = -1
    report = {
        "股票代码": code,
        "股票名称": name,
        "数据区间": f"{dates[0]} ~ {dates[-1]}",
        "分析日期": dates[-1],
    }
    
    # 价格
    report["最新价"] = closes[latest_idx]
    if len(closes) >= 2:
        report["涨跌幅%"] = round(pct_changes[latest_idx], 2) if pct_changes[latest_idx] else None
    if len(opens) >= 2:
        report["开盘价"] = opens[latest_idx]
        report["最高价"] = highs[latest_idx]
        report["最低价"] = lows[latest_idx]
    report["成交量(手)"] = volumes[latest_idx]
    report["成交额(元)"] = amounts[latest_idx]
    report["换手率%"] = turnovers[latest_idx]
    
    # 均线
    report["MA5"] = round(ma5[latest_idx], 2) if ma5[latest_idx] else None
    report["MA10"] = round(ma10[latest_idx], 2) if ma10[latest_idx] else None
    report["MA20"] = round(ma20[latest_idx], 2) if ma20[latest_idx] else None
    report["MA60"] = round(ma60[latest_idx], 2) if ma60[latest_idx] else None
    
    # 均线形态
    if ma5[latest_idx] and ma10[latest_idx] and ma20[latest_idx]:
        if ma5[latest_idx] > ma10[latest_idx] > ma20[latest_idx]:
            report["均线形态"] = "多头排列（偏多）"
        elif ma5[latest_idx] < ma10[latest_idx] < ma20[latest_idx]:
            report["均线形态"] = "空头排列（偏空）"
        else:
            report["均线形态"] = "均线缠绕（震荡）"
    
    # 价格位置
    if ma20[latest_idx]:
        pct_from_ma20 = round((closes[latest_idx] - ma20[latest_idx]) / ma20[latest_idx] * 100, 2)
        report["偏离MA20%"] = pct_from_ma20
    
    if ma60[latest_idx]:
        pct_from_ma60 = round((closes[latest_idx] - ma60[latest_idx]) / ma60[latest_idx] * 100, 2)
        report["偏离MA60%"] = pct_from_ma60
    
    # 成交量分析
    if len(volumes) >= 5:
        vol_ma5 = sum(volumes[-6:-1]) / 5 if len(volumes) >= 6 else sum(volumes[-5:]) / 5
        vol_ratio = round(volumes[latest_idx] / vol_ma5, 2) if vol_ma5 > 0 else None
        report["量比(5日)"] = vol_ratio
        if vol_ratio:
            if vol_ratio > 2:
                report["成交量判断"] = "放量"
            elif vol_ratio > 1.5:
                report["成交量判断"] = "温和放量"
            elif vol_ratio < 0.5:
                report["成交量判断"] = "缩量"
            else:
                report["成交量判断"] = "正常"
    
    # RSI
    report["RSI(6)"] = rsi6[latest_idx]
    report["RSI(14)"] = rsi14[latest_idx] if rsi14[latest_idx] else None
    if rsi14[latest_idx]:
        r14 = rsi14[latest_idx]
        if r14 > 80:
            report["RSI判断"] = "超买（偏空）"
        elif r14 < 20:
            report["RSI判断"] = "超卖（偏多）"
        elif r14 > 50:
            report["RSI判断"] = "偏强"
        else:
            report["RSI判断"] = "偏弱"
    
    # MACD
    if dif[latest_idx] is not None:
        report["MACD_DIF"] = round(dif[latest_idx], 4)
        report["MACD_DEA"] = round(dea[latest_idx], 4)
        report["MACD_BAR"] = round(macd_bar[latest_idx], 4)
        if dif[latest_idx] > dea[latest_idx]:
            report["MACD判断"] = "金叉/多头"
        else:
            report["MACD判断"] = "死叉/空头"
    
    # KDJ
    if k_values[latest_idx] is not None:
        report["KDJ_K"] = k_values[latest_idx]
        report["KDJ_D"] = d_values[latest_idx]
        report["KDJ_J"] = j_values[latest_idx]
        if k_values[latest_idx] > 80 and d_values[latest_idx] > 80:
            report["KDJ判断"] = "超买区"
        elif k_values[latest_idx] < 20 and d_values[latest_idx] < 20:
            report["KDJ判断"] = "超卖区"
        elif k_values[latest_idx] > d_values[latest_idx]:
            report["KDJ判断"] = "金叉（偏多）"
        else:
            report["KDJ判断"] = "死叉（偏空）"
    
    # 近期表现（如有足够数据）
    if len(closes) >= 5:
        report["近5日涨跌%"] = round((closes[-1] / closes[-5] - 1) * 100, 2) if closes[-5] else None
    if len(closes) >= 21:
        report["近20日涨跌%"] = round((closes[-1] / closes[-21] - 1) * 100, 2) if closes[-21] else None
    
    # 深度分析
    if deep:
        # 价格区间
        if len(closes) >= 60:
            high_60d = max(highs[-60:])
            low_60d = min(lows[-60:])
            report["60日最高"] = high_60d
            report["60日最低"] = low_60d
            pos = round((closes[-1] - low_60d) / (high_60d - low_60d) * 100, 1) if high_60d != low_60d else 50
            report["60日位置%"] = pos
        
        if len(closes) >= 250:
            high_250d = max(highs[-250:])
            low_250d = min(lows[-250:])
            report["250日最高"] = high_250d
            report["250日最低"] = low_250d
            pos = round((closes[-1] - low_250d) / (high_250d - low_250d) * 100, 1) if high_250d != low_250d else 50
            report["250日位置%"] = pos
        
        # 年平均成交额
        if len(amounts) >= 250:
            avg_amount = sum(a for a in amounts[-250:] if a) / 250
            report["年日均成交额(万)"] = round(avg_amount / 10000, 0)
    
    return report


def print_report(report):
    """打印分析报告"""
    print("\n" + "=" * 60)
    print(f"  📊 {report['股票名称']}（{report['股票代码']}）技术分析报告")
    print("=" * 60)
    
    # 分组打印
    groups = [
        ("📌 基本信息", ["数据区间", "分析日期"]),
        ("💰 价格信息", ["最新价", "涨跌幅%", "开盘价", "最高价", "最低价"]),
        ("📊 成交信息", ["成交量(手)", "成交额(元)", "换手率%", "量比(5日)", "成交量判断"]),
        ("📈 均线系统", ["MA5", "MA10", "MA20", "MA60", "均线形态", "偏离MA20%", "偏离MA60%"]),
        ("📉 技术指标", ["RSI(6)", "RSI(14)", "RSI判断", "MACD_DIF", "MACD_DEA", "MACD_BAR", "MACD判断", "KDJ_K", "KDJ_D", "KDJ_J", "KDJ判断"]),
        ("📅 近期表现", ["近5日涨跌%", "近20日涨跌%"]),
    ]
    
    for group_name, keys in groups:
        print(f"\n  {group_name}")
        for k in keys:
            v = report.get(k)
            if v is not None:
                if isinstance(v, float):
                    print(f"    {k}: {v:,.2f}")
                else:
                    print(f"    {k}: {v}")
    
    # 深度部分
    deep_keys = ["60日最高", "60日最低", "60日位置%", "250日最高", "250日最低", "250日位置%", "年日均成交额(万)"]
    shown = False
    for k in deep_keys:
        if report.get(k) is not None:
            if not shown:
                print(f"\n  🔍 深度分析")
                shown = True
            v = report[k]
            if isinstance(v, float):
                print(f"    {k}: {v:,.2f}")
            else:
                print(f"    {k}: {v}")
    
    print("\n" + "=" * 60)
    
    # 生成可供 LM Studio 分析的摘要文本
    summary = "\n--- 以下为结构化摘要，可直接喂给AI分析 ---\n\n"
    summary += f"股票: {report['股票名称']}({report['股票代码']})\n"
    summary += f"最新价: {report.get('最新价', 'N/A')}, 涨跌幅: {report.get('涨跌幅%', 'N/A')}%\n"
    summary += f"均线: MA5={report.get('MA5')}, MA10={report.get('MA10')}, MA20={report.get('MA20')}\n"
    summary += f"均线形态: {report.get('均线形态', 'N/A')}\n"
    summary += f"RSI(14): {report.get('RSI(14)', 'N/A')}, {report.get('RSI判断', '')}\n"
    summary += f"MACD: DIF={report.get('MACD_DIF')}, DEA={report.get('MACD_DEA')}, {report.get('MACD判断', '')}\n"
    summary += f"KDJ: K={report.get('KDJ_K')}, D={report.get('KDJ_D')}, J={report.get('KDJ_J')}, {report.get('KDJ判断', '')}\n"
    summary += f"成交量: {report.get('成交量判断', 'N/A')}, 量比={report.get('量比(5日)', 'N/A')}\n"
    
    print(summary)
    
    return summary


def scan_market(conn, condition="oversold"):
    """扫描全市场找出符合技术条件的股票"""
    c = conn.cursor()
    c.execute("SELECT DISTINCT code FROM stocks")
    all_codes = [r[0] for r in c.fetchall()]
    
    print(f"\n扫描 {len(all_codes)} 只股票，筛选条件: {condition}...")
    
    results = []
    for i, code in enumerate(all_codes):
        if i % 500 == 0:
            print(f"  进度: {i}/{len(all_codes)}")
        
        report = analyze_stock(conn, code, deep=False)
        if report is None:
            continue
        
        # 条件筛选
        match = False
        if condition == "oversold":
            # RSI < 30 超卖
            if report.get("RSI(14)") and report["RSI(14)"] < 30:
                match = True
        elif condition == "overbought":
            if report.get("RSI(14)") and report["RSI(14)"] > 70:
                match = True
        elif condition == "golden_cross":
            # MACD 金叉
            if report.get("MACD判断") == "金叉/多头":
                match = True
        elif condition == "breakout":
            # 站上MA20且量比>1.5
            if report.get("均线形态") == "多头排列（偏多）" and report.get("量比(5日)") and report["量比(5日)"] > 1.5:
                match = True
        
        if match:
            results.append(report)
    
    # 结果按RSI排序
    if condition in ("oversold",):
        results.sort(key=lambda x: x.get("RSI(14)", 100))
    else:
        results.sort(key=lambda x: x.get("涨跌幅%") or 0, reverse=True)
    
    # 输出前20
    print(f"\n找到 {len(results)} 只符合条件的股票，显示前20:\n")
    for i, r in enumerate(results[:20]):
        print(f"{i+1}. {r['股票代码']} {r['股票名称']} | "
              f"价格:{r.get('最新价')} | "
              f"涨跌:{r.get('涨跌幅%')}% | "
              f"RSI:{r.get('RSI(14)')}")
    
    # 保存完整列表
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    output_file = os.path.join(OUTPUT_DIR, f"scan_{condition}_{datetime.now().strftime('%Y%m%d')}.json")
    with open(output_file, "w") as f:
        json.dump(results[:50], f, ensure_ascii=False, indent=2, default=str)
    print(f"\n完整结果已保存: {output_file}")
    
    return results


def top_n(conn, n=20, metric="pct_change", days=20):
    """涨幅/跌幅排行榜"""
    c = conn.cursor()
    
    # 获取最近N天的数据
    c.execute("SELECT DISTINCT date FROM stocks ORDER BY date DESC LIMIT ?", (days,))
    recent_dates = [r[0] for r in c.fetchall()]
    start_date = recent_dates[-1] if recent_dates else None
    
    if not start_date:
        print("[错误] 数据库中没有足够的数据")
        return
    
    c.execute("""
        SELECT s1.code, s1.name, 
               s1.close as latest_close,
               s2.close as old_close,
               ROUND((s1.close - s2.close) / s2.close * 100, 2) as total_pct,
               ROUND(s1.close, 2) as price
        FROM stocks s1
        JOIN stocks s2 ON s1.code = s2.code AND s2.date = ?
        WHERE s1.date = (SELECT MAX(date) FROM stocks WHERE code = s1.code)
        AND s2.close > 0
        ORDER BY total_pct DESC
        LIMIT ?
    """, (start_date, n))
    
    rows = c.fetchall()
    
    print(f"\n📈 近{days}日涨幅前{n}:\n")
    for i, row in enumerate(rows):
        code, name, close, old_close, pct, price = row
        print(f"  {i+1}. {code} {name} | 现价:{price} | 涨幅:{pct}%")
    
    return rows


def main():
    parser = argparse.ArgumentParser(description="离线股票分析工具")
    parser.add_argument("code", nargs="?", help="股票代码（如 600519）")
    parser.add_argument("--deep", action="store_true", help="深度分析（含长期指标）")
    parser.add_argument("--scan", action="store_true", help="扫描全市场")
    parser.add_argument("--condition", type=str, default="oversold",
                       choices=["oversold", "overbought", "golden_cross", "breakout"],
                       help="扫描条件: oversold(超卖), overbought(超买), golden_cross(金叉), breakout(突破)")
    parser.add_argument("--top20", action="store_true", help="近20日涨幅前20")
    parser.add_argument("--bottom20", action="store_true", help="近20日跌幅前20")
    parser.add_argument("--days", type=int, default=20, help="排行榜天数")
    parser.add_argument("--output", type=str, help="输出JSON文件路径")
    
    args = parser.parse_args()
    
    conn = get_conn()
    
    if args.scan:
        scan_market(conn, args.condition)
    elif args.top20:
        top_n(conn, 20, "pct_change", args.days)
    elif args.bottom20:
        top_n(conn, 20, "pct_change", args.days)  # reverse in query
    elif args.code:
        report = analyze_stock(conn, args.code, deep=args.deep)
        if report:
            summary = print_report(report)
            
            if args.output:
                with open(args.output, "w") as f:
                    json.dump(report, f, ensure_ascii=False, indent=2, default=str)
                print(f"报告已保存: {args.output}")
    else:
        parser.print_help()
    
    conn.close()


if __name__ == "__main__":
    # 兼容旧版 import
    from datetime import datetime
    main()
