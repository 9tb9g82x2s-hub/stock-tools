#!/usr/bin/env python3
"""
牛股启动前 OBV 特征批量统计
=============================
筛选近N天涨幅≥X%的股票，提取启动前的OBV特征，统计共性规律。

用法：
    # 基础统计（纯Python，不需要模型）
    /usr/bin/python3 ~/stock-tools/batch_stats.py --gain 20 --days 30

    # 统计 + AI解读
    /usr/bin/python3 ~/stock-tools/batch_stats.py --gain 20 --days 30 --ai

    # 自定义参数
    /usr/bin/python3 ~/stock-tools/batch_stats.py --gain 15 --days 20 --pre-window 15 --ai
"""

import sys
import os
import json
import argparse
import sqlite3
import urllib.request
import urllib.error
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze import get_conn, calc_ma, calc_rsi, calc_macd, calc_kdj, calc_obv, obv_features

DB_PATH = os.path.expanduser("~/stock-data/all_stocks.db")
OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "qwen2.5:14b-ctx"


# ────────────────────────────────────────────
# 1. 数据读取与筛选
# ────────────────────────────────────────────
def get_stock_data(code, min_days=90):
    """读取单只股票最近N天数据（从旧到新）"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT date, open, high, low, close, volume, pct_change
        FROM stocks WHERE code=? ORDER BY date DESC LIMIT ?
    """, (code, min_days))
    rows = c.fetchall()
    conn.close()
    if len(rows) < min_days:
        return None
    return list(reversed(rows))


def calc_gain(closes, days):
    """计算最近N天的累计涨幅"""
    if len(closes) <= days:
        return None
    return (closes[-1] / closes[-days - 1] - 1) * 100


# ────────────────────────────────────────────
# 2. 启动前 OBV 特征提取
# ────────────────────────────────────────────
def extract_pre_launch_features(rows, gain_days=30, pre_window=20, gap=5):
    """
    提取启动前的OBV特征。

    时间轴示意图（从旧到新）：
    |------ 启动前段 ------| 间隙 |---- 涨幅段 ----|
    [-pre_window-gap-gain_days : -gain_days-gap]      [-gain_days:]

    gap: 在启动前段和涨幅段之间留的间隔天数，避免边界效应
    """
    n = len(rows)
    closes = [r[4] for r in rows]
    volumes = [r[5] for r in rows]
    highs = [r[2] for r in rows]
    lows = [r[3] for r in rows]

    # 涨幅段（最近 gain_days 天）
    gain_end = n
    gain_start = n - gain_days

    # 启动前段
    pre_end = gain_start - gap
    pre_start = pre_end - pre_window

    if pre_start < 0:
        return None

    pre_closes = closes[pre_start:pre_end]
    pre_volumes = volumes[pre_start:pre_end]
    pre_highs = highs[pre_start:pre_end]
    pre_lows = lows[pre_start:pre_end]

    # 计算启动前段的 OBV 特征
    feat = obv_features(pre_closes, pre_volumes, window=pre_window)
    if feat is None:
        return None

    # 补充更多特征
    # 启动前价格走势
    pre_price_chg = (pre_closes[-1] / pre_closes[0] - 1) * 100 if pre_closes[0] else 0

    # 启动前成交量均值
    pre_vol_avg = sum(v for v in pre_volumes if v) / len(pre_volumes)

    # 涨幅段成交量均值（对比用）
    gain_volumes = volumes[gain_start:gain_end]
    gain_vol_avg = sum(v for v in gain_volumes if v) / len(gain_volumes) if gain_volumes else 0

    # 量比：涨幅段/启动前段
    vol_ratio = gain_vol_avg / pre_vol_avg if pre_vol_avg else 1

    feat.update({
        "pre_price_chg": round(pre_price_chg, 2),
        "pre_price_trend": "上涨" if pre_price_chg > 2 else ("下跌" if pre_price_chg < -2 else "横盘"),
        "pre_vol_avg": round(pre_vol_avg, 0),
        "gain_vol_avg": round(gain_vol_avg, 0),
        "vol_ratio_gain_vs_pre": round(vol_ratio, 2),
        "vol_surge": vol_ratio > 2.0,
    })

    return feat


# ────────────────────────────────────────────
# 3. 批量扫描
# ────────────────────────────────────────────
def batch_scan(gain_pct=20, gain_days=30, pre_window=20):
    """
    扫描全市场，找出涨幅达标的股票，提取启动前特征。
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT DISTINCT code FROM stocks")
    all_codes = [r[0] for r in c.fetchall()]
    conn.close()

    total = len(all_codes)
    min_days = gain_days + pre_window + 10
    print(f"📊 扫描 {total} 只股票（近{gain_days}天涨幅≥{gain_pct}%，启动前窗口{pre_window}天）\n")

    results = []
    no_data = 0
    not_enough = 0

    for i, code in enumerate(all_codes):
        if (i + 1) % 500 == 0:
            print(f"  进度: {i+1}/{total} (命中: {len(results)})")

        rows = get_stock_data(code, min_days)
        if rows is None:
            not_enough += 1
            continue

        closes = [r[4] for r in rows]
        gain = calc_gain(closes, gain_days)
        if gain is None or gain < gain_pct:
            continue

        # 获取股票名称
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT name FROM stocks WHERE code=? LIMIT 1", (code,))
        name_row = c.fetchone()
        name = name_row[0] if name_row else code
        # 也查 stock_list
        if name == code:
            c.execute("SELECT name FROM stock_list WHERE code=?", (code,))
            sl_row = c.fetchone()
            if sl_row:
                name = sl_row[0]
        conn.close()

        # 提取启动前特征
        feat = extract_pre_launch_features(rows, gain_days, pre_window)
        if feat is None:
            continue

        results.append({
            "code": code,
            "name": name,
            "gain_pct": round(gain, 2),
            "latest_price": closes[-1],
            "features": feat,
        })

    print(f"\n  完成！命中: {len(results)} | 数据不足: {not_enough}")
    return results


# ────────────────────────────────────────────
# 4. 统计汇总
# ────────────────────────────────────────────
def summarize(results, gain_pct, gain_days, pre_window):
    """对结果做统计汇总"""
    n = len(results)
    if n == 0:
        return "没有找到符合条件的股票。"

    # 特征计数
    obv_up = sum(1 for r in results if r["features"]["obv_trend"] == "上升")
    obv_down = sum(1 for r in results if r["features"]["obv_trend"] == "下降")
    obv_flat = sum(1 for r in results if r["features"]["obv_trend"] == "横盘")

    obv_breakout = sum(1 for r in results if r["features"]["obv_breakout"])
    divergence = sum(1 for r in results if r["features"]["divergence"])
    div_buy = sum(1 for r in results if "吸筹" in r["features"]["divergence_type"])
    div_sell = sum(1 for r in results if "出货" in r["features"]["divergence_type"])

    vol_expand = sum(1 for r in results if r["features"]["vol_expand"])
    vol_surge = sum(1 for r in results if r["features"]["vol_surge"])

    # 价格启动前走势
    pre_up = sum(1 for r in results if r["features"]["pre_price_trend"] == "上涨")
    pre_down = sum(1 for r in results if r["features"]["pre_price_trend"] == "下跌")
    pre_flat = sum(1 for r in results if r["features"]["pre_price_trend"] == "横盘")

    # 平均 OBV 斜率
    avg_slope = sum(r["features"]["obv_slope"] for r in results) / n

    # 平均量比
    avg_vol_ratio = sum(r["features"]["vol_ratio_gain_vs_pre"] for r in results) / n

    # OBV斜率分布
    slopes = [r["features"]["obv_slope"] for r in results]
    slopes.sort()

    report = f"""近{gain_days}天涨幅≥{gain_pct}%的股票共 {n} 只

═══════════════════════════════
一、启动前 OBV 趋势分布
═══════════════════════════════
  OBV上升: {obv_up} 只 ({obv_up/n*100:.1f}%)
  OBV横盘: {obv_flat} 只 ({obv_flat/n*100:.1f}%)
  OBV下降: {obv_down} 只 ({obv_down/n*100:.1f}%)
  平均OBV归一化斜率: {avg_slope:.2f}
  （斜率>0表示启动前OBV在缓慢攀升）

═══════════════════════════════
二、启动前价量背离
═══════════════════════════════
  出现背离: {divergence} 只 ({divergence/n*100:.1f}%)
  其中吸筹型（价跌OBV升）: {div_buy} 只
  其中出货型（价升OBV跌）: {div_sell} 只

═══════════════════════════════
三、启动前 OBV 突破
═══════════════════════════════
  OBV提前突破: {obv_breakout} 只 ({obv_breakout/n*100:.1f}%)

═══════════════════════════════
四、成交量特征
═══════════════════════════════
  启动前温和放量（量比>1.5）: {vol_expand} 只 ({vol_expand/n*100:.1f}%)
  爆发放量（涨幅段/启动前>2）: {vol_surge} 只 ({vol_surge/n*100:.1f}%)
  平均量比（涨幅段/启动前）: {avg_vol_ratio:.2f}

═══════════════════════════════
五、启动前价格走势
═══════════════════════════════
  启动前上涨（提前启动）: {pre_up} 只 ({pre_up/n*100:.1f}%)
  启动前横盘整理: {pre_flat} 只 ({pre_flat/n*100:.1f}%)
  启动前仍在下跌: {pre_down} 只 ({pre_down/n*100:.1f}%)

═══════════════════════════════
六、典型个股（涨幅前10）
═══════════════════════════════
"""
    top = sorted(results, key=lambda x: x["gain_pct"], reverse=True)[:10]
    for i, r in enumerate(top):
        f = r["features"]
        report += (
            f"  {i+1}. {r['code']} {r['name']} "
            f"| 涨幅:{r['gain_pct']}% "
            f"| OBV:{f['obv_trend']}(斜率{f['obv_slope']}) "
            f"| 背离:{f['divergence_type']} "
            f"| 突破:{'是' if f['obv_breakout'] else '否'}\n"
        )

    return report


# ────────────────────────────────────────────
# 5. AI 解读
# ────────────────────────────────────────────
def ai_interpret(report, model=DEFAULT_MODEL):
    """让 Ollama 解读统计结果，给出规律总结和交易启发"""
    prompt = f"""你是一位量化分析专家。以下是A股市场中「牛股启动前OBV特征」的批量统计结果。请基于数据给出专业解读。

{report}

请从以下角度解读：

1. **核心发现**：启动前OBV最显著的共性特征是什么？（用数据说话）
2. **规律总结**：牛股启动前，OBV通常呈现什么模式？价量关系有什么特点？
3. **实战启发**：如果一个交易者想用OBV提前发现潜在牛股，应该关注哪些信号？
4. **注意事项**：这个统计可能有哪些局限性？（样本偏差、时间窗口选择等）

请用中文，结论先行，简洁专业。"""

    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": {"temperature": 0.3, "num_predict": 2048},
    }).encode("utf-8")

    req = urllib.request.Request(OLLAMA_URL, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")

    print(f"\n{'='*60}")
    print(f"  🤖 {model} AI 解读中...")
    print(f"{'='*60}\n")

    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            for line in resp:
                line = line.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    text = chunk.get("response", "")
                    if text:
                        print(text, end="", flush=True)
                    if chunk.get("done"):
                        break
                except json.JSONDecodeError:
                    continue
        print("\n")
    except urllib.error.URLError as e:
        print(f"\n[提示] Ollama 未运行，跳过 AI 解读。统计报告见上方。")
        print(f"       启动 Ollama 后重新加 --ai 参数即可。")


# ────────────────────────────────────────────
# 6. 主入口
# ────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="牛股启动前 OBV 特征批量统计",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  /usr/bin/python3 ~/stock-tools/batch_stats.py --gain 20 --days 30
  /usr/bin/python3 ~/stock-tools/batch_stats.py --gain 15 --days 20 --ai
  /usr/bin/python3 ~/stock-tools/batch_stats.py --gain 20 --days 30 --pre-window 15 --ai
        """
    )
    parser.add_argument("--gain", type=float, default=20,
                        help="涨幅阈值%%，默认20")
    parser.add_argument("--days", type=int, default=30,
                        help="涨幅统计天数，默认30")
    parser.add_argument("--pre-window", type=int, default=20,
                        help="启动前分析窗口天数，默认20")
    parser.add_argument("--ai", action="store_true",
                        help="统计完成后让本地 Ollama 做 AI 解读")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"AI 模型名称（默认: {DEFAULT_MODEL}）")
    parser.add_argument("--output", type=str,
                        help="保存报告到文件（JSON格式）")

    args = parser.parse_args()

    # Step 1: 批量扫描
    results = batch_scan(
        gain_pct=args.gain,
        gain_days=args.days,
        pre_window=args.pre_window,
    )

    if not results:
        print(f"\n没有找到近{args.days}天涨幅≥{args.gain}%的股票。")
        print("可能原因：数据库数据量不足，请先运行下载器。")
        return

    # Step 2: 统计汇总
    report = summarize(results, args.gain, args.days, args.pre_window)
    print("\n" + report)

    # Step 3: 保存
    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(results, f, ensure_ascii=False, indent=2, default=str)
        print(f"详细数据已保存: {args.output}")

    # Step 4: AI 解读
    if args.ai:
        ai_interpret(report, args.model)


if __name__ == "__main__":
    main()
