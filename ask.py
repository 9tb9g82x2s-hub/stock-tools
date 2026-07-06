#!/usr/bin/env python3
"""
自然语言股票问答 — 完全离线
==============================
飞机上想到任何角度，直接在终端里问。
自动读数据库 → 提取相关数据 → 喂给 Ollama → 输出答案。

用法：
    # 交互模式
    /usr/bin/python3 ~/stock-tools/ask.py

    # 单次提问
    /usr/bin/python3 ~/stock-tools/ask.py "近30天涨幅前10的股票有哪些共同特征"

    # 指定模型
    /usr/bin/python3 ~/stock-tools/ask.py --model deepseek-r1:14b
"""

import sys
import os
import re
import json
import sqlite3
import urllib.request
import urllib.error
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze import (
    analyze_stock, get_conn, calc_ma, calc_rsi, calc_macd, calc_kdj,
    calc_obv, obv_features,
)

DB_PATH = os.path.expanduser("~/stock-data/all_stocks.db")
OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "qwen2.5:14b-ctx"

# ────────────────────────────────────────────
# 数据层：从数据库提取信息
# ────────────────────────────────────────────
def market_overview():
    """全市场概况"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("SELECT COUNT(DISTINCT code) FROM stocks")
    total = c.fetchone()[0]

    c.execute("SELECT MIN(date), MAX(date) FROM stocks")
    d1, d2 = c.fetchone()

    # 最近一个交易日
    c.execute("""
        SELECT s.code, s.name, s.close, s.pct_change, s.volume, s.turnover
        FROM stocks s
        WHERE s.date = (SELECT MAX(date) FROM stocks WHERE code = s.code)
        ORDER BY s.pct_change DESC
    """)
    latest = c.fetchall()

    # 统计涨跌家数
    up = sum(1 for r in latest if r[3] and r[3] > 0)
    down = sum(1 for r in latest if r[3] and r[3] < 0)
    flat = sum(1 for r in latest if r[3] and r[3] == 0)

    # 涨幅前5 / 跌幅前5
    sorted_by_gain = sorted([r for r in latest if r[3] is not None], key=lambda x: x[3], reverse=True)
    top5 = sorted_by_gain[:5]
    bottom5 = sorted_by_gain[-5:][::-1]

    conn.close()

    overview = f"""## 市场概况
- 数据库共有 {total} 只股票的历史数据
- 数据区间: {d1} ~ {d2}
- 最近交易日涨跌: 涨{up} / 跌{down} / 平{flat}
- 涨幅前5: {', '.join(f'{r[0]} {r[1]}({r[3]:+.1f}%)' for r in top5)}
- 跌幅前5: {', '.join(f'{r[0]} {r[1]}({r[3]:+.1f}%)' for r in bottom5)}
"""
    return overview, total


def search_stock(keyword):
    """按代码或名称模糊搜索股票"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # 精确代码匹配
    if re.match(r'^\d{6}$', keyword):
        c.execute("SELECT code, name FROM stock_list WHERE code=?", (keyword,))
        row = c.fetchone()
        if row:
            conn.close()
            return [row]

    # 模糊名称搜索
    c.execute("SELECT code, name FROM stock_list WHERE name LIKE ? LIMIT 10",
              (f"%{keyword}%",))
    rows = c.fetchall()
    conn.close()
    return rows


def stock_snapshot(code):
    """单只股票快照"""
    conn = get_conn()
    report = analyze_stock(conn, code, deep=False)
    conn.close()

    if report is None:
        return None

    rows = [
        f"- {k}: {v}"
        for k, v in report.items()
        if v is not None and k not in ("数据区间", "股票代码", "股票名称")
    ]
    return f"## {report.get('股票名称', code)}（{code}）技术指标\n" + "\n".join(rows)


def top_gainers(n=10, days=20):
    """涨幅排行"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT DISTINCT date FROM stocks ORDER BY date DESC LIMIT ?", (days + 1,))
    dates = [r[0] for r in c.fetchall()]
    if len(dates) < days + 1:
        conn.close()
        return "数据不足"

    old_date = dates[-1]

    c.execute("""
        SELECT s1.code, s1.name,
               ROUND((s1.close - s2.close) / s2.close * 100, 2) as gain,
               ROUND(s1.close, 2) as price
        FROM stocks s1
        JOIN stocks s2 ON s1.code = s2.code AND s2.date = ?
        WHERE s1.date = (SELECT MAX(date) FROM stocks WHERE code = s1.code)
        AND s2.close > 0
        ORDER BY gain DESC LIMIT ?
    """, (old_date, n))
    rows = c.fetchall()
    conn.close()

    return "\n".join(
        f"  {i+1}. {r[0]} {r[1]} | 涨幅:{r[2]:+.1f}% | 现价:{r[3]:.2f}"
        for i, r in enumerate(rows)
    )


# ────────────────────────────────────────────
# 核心：解析问题 → 提取数据 → 问 Ollama
# ────────────────────────────────────────────
def analyze_question(question):
    """解析用户问题，识别需要什么数据"""
    data_sections = []
    codes = set(re.findall(r'\b(\d{6})\b', question))

    # 0. 市场概况（每次必带）
    overview, total = market_overview()
    data_sections.append(overview)

    # 1. 股票代码 → 读技术指标
    for code in codes:
        snap = stock_snapshot(code)
        if snap:
            data_sections.append(snap)

    # 2. 涨幅排行相关
    if re.search(r'涨幅|涨.*排|领涨|前\d|top', question):
        days_match = re.search(r'(\d+)\s*[日天]', question)
        days = int(days_match.group(1)) if days_match else 20
        gainers = top_gainers(10, days)
        data_sections.append(f"## 近{days}天涨幅前10\n{gainers}")

    # 3. 跌排行（如果问了跌的也给出）
    if re.search(r'跌幅|跌.*排|领跌', question):
        days_match = re.search(r'(\d+)\s*[日天]', question)
        days = int(days_match.group(1)) if days_match else 20
        data_sections.append(f"## 近{days}天跌幅前10\n{top_gainers(10, days)}")

    # 4. 搜索提到的股票名
    name_matches = re.findall(r'[\u4e00-\u9fa5]{2,4}(?:股份|科技|医药|银行|证券|酒|能源|汽车|电子)', question)
    for name in set(name_matches):
        results = search_stock(name)
        if results and len(results) <= 5:
            for r in results:
                snap = stock_snapshot(r[0])
                if snap:
                    data_sections.append(snap)

    return data_sections, total


def ask_ollama(question, data_sections, model=DEFAULT_MODEL):
    """把问题 + 数据发给 Ollama"""

    data_text = "\n\n".join(data_sections)

    prompt = f"""你是A股量化分析助手。以下是当前数据库中的实际数据，请基于这些数据回答用户的问题。

{data_text}

---
用户问题: {question}
---

回答要求：
1. 如果数据充分，给出基于数据的明确分析
2. 如果数据不足以回答，诚实说明缺少什么数据
3. 如果涉及预测，注明是基于历史规律的推测，不是确定性结论
4. 用中文，简洁专业，结论先行
"""

    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": {"temperature": 0.3, "num_predict": 2048},
    }).encode("utf-8")

    req = urllib.request.Request(OLLAMA_URL, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")

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
        print()
    except urllib.error.URLError:
        print("\n[提示] 无法连接 Ollama，请确保 Ollama 正在运行")
    except Exception as e:
        print(f"\n[错误] {e}")


# ────────────────────────────────────────────
# 交互模式
# ────────────────────────────────────────────
def interactive(model):
    _, total = market_overview()

    print(f"\n{'='*60}")
    print(f"  ✈️  离线股票问答（{total} 只股票 | {model}）")
    print(f"  输入问题开始分析，输入 quit 退出")
    print(f"{'='*60}\n")

    print("提示：可以问这些——")
    print("  • 600519最近走势怎么样")
    print("  • 近20天涨幅最大的10只股票")
    print("  • 哪些股票MACD刚金叉")
    print("  • 永鼎股份的OBV有什么特征")
    print("  • 对比600519和000858的技术面")
    print()

    while True:
        try:
            question = input("🔍 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not question:
            continue
        if question.lower() in ("quit", "exit", "q", "退出"):
            print("再见！")
            break

        # 解析 + 提取数据 + 问 Ollama
        print()
        data_sections, _ = analyze_question(question)
        ask_ollama(question, data_sections, model)
        print()


# ────────────────────────────────────────────
# 主入口
# ────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="自然语言股票问答（离线）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  /usr/bin/python3 ~/stock-tools/ask.py                              # 交互模式
  /usr/bin/python3 ~/stock-tools/ask.py "茅台技术面怎么样"            # 单次提问
  /usr/bin/python3 ~/stock-tools/ask.py --model deepseek-r1:14b      # 指定模型
        """
    )
    parser.add_argument("question", nargs="?", default=None, help="要问的问题（不填进入交互模式）")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"模型名称（默认: {DEFAULT_MODEL}）")
    args = parser.parse_args()

    if args.question:
        # 单次模式
        data_sections, _ = analyze_question(args.question)
        ask_ollama(args.question, data_sections, args.model)
    else:
        interactive(args.model)


if __name__ == "__main__":
    main()
