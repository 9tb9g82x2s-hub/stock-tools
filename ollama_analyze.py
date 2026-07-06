#!/usr/bin/env python3
"""
Ollama 股票深度分析 — 一键脚本
=================================
读取本地数据库 + 技术指标 → 喂给本地 Ollama 模型 → 输出投资建议

用法（在终端复制粘贴即可）:
    cd ~/stock-tools && source .venv/bin/activate && python ollama_analyze.py 600519
    cd ~/stock-tools && source .venv/bin/activate && python ollama_analyze.py 600105 --model deepseek-r1:14b
"""

import sys
import os
import json
import argparse
import sqlite3
import urllib.request
import urllib.error

# 把 analyze.py 所在目录加入搜索路径，方便 import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze import analyze_stock, get_conn

OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "qwen2.5:14b-ctx"
DB_PATH = os.path.expanduser("~/stock-data/all_stocks.db")


# ──────────────────────────────────────────────
# 1. 从数据库读取最近K线数据
# ──────────────────────────────────────────────
def get_recent_klines(code, days=60):
    """读取最近 N 天的日线数据（返回从旧到新排列）"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT date, open, high, low, close, volume, pct_change, turnover
        FROM stocks WHERE code=? ORDER BY date DESC LIMIT ?
    """, (code, days))
    rows = c.fetchall()
    conn.close()
    return list(reversed(rows))  # 翻转为从旧到新


# ──────────────────────────────────────────────
# 2. 构建给 AI 的分析提示词
# ──────────────────────────────────────────────
def build_prompt(code, report, klines):
    """把技术指标 + K线数据拼成 AI 能理解的提示词"""

    name = report.get("股票名称", code)

    # ── 技术指标摘要 ──
    indicators = f"""
## 技术指标快照
- 股票: {name}（{code}）
- 最新价: {report.get('最新价', 'N/A')}
- 涨跌幅: {report.get('涨跌幅%', 'N/A')}%
- 开盘/最高/最低: {report.get('开盘价')}/{report.get('最高价')}/{report.get('最低价')}
- 均线: MA5={report.get('MA5')}, MA10={report.get('MA10')}, MA20={report.get('MA20')}, MA60={report.get('MA60')}
- 均线形态: {report.get('均线形态', 'N/A')}
- 偏离MA20: {report.get('偏离MA20%', 'N/A')}%
- 偏离MA60: {report.get('偏离MA60%', 'N/A')}%
- RSI(6)={report.get('RSI(6)')}, RSI(14)={report.get('RSI(14)')} → {report.get('RSI判断', '')}
- MACD: DIF={report.get('MACD_DIF')}, DEA={report.get('MACD_DEA')}, BAR={report.get('MACD_BAR')} → {report.get('MACD判断', '')}
- KDJ: K={report.get('KDJ_K')}, D={report.get('KDJ_D')}, J={report.get('KDJ_J')} → {report.get('KDJ判断', '')}
- 成交量: {report.get('成交量判断', 'N/A')}（量比={report.get('量比(5日)', 'N/A')}）
- 换手率: {report.get('换手率%', 'N/A')}%
- 近5日涨跌: {report.get('近5日涨跌%', 'N/A')}%
- 近20日涨跌: {report.get('近20日涨跌%', 'N/A')}%
"""

    # 深度指标（如果有）
    deep_extra = ""
    for key, label in [
        ("60日最高", "60日最高"), ("60日最低", "60日最低"), ("60日位置%", "60日位置%"),
        ("250日最高", "250日最高"), ("250日最低", "250日最低"), ("250日位置%", "250日位置%"),
        ("年日均成交额(万)", "年日均成交额(万)")
    ]:
        if report.get(key) is not None:
            deep_extra += f"- {label}: {report[key]}\n"
    if deep_extra:
        indicators += f"\n## 深度指标\n{deep_extra}"

    # ── 最近K线表格（取最近20根） ──
    kline_table = "\n## 最近20个交易日K线（从旧到新）\n"
    kline_table += "| 日期 | 开盘 | 最高 | 最低 | 收盘 | 成交量(手) | 涨跌幅% |\n"
    kline_table += "|------|------|------|------|------|-----------|--------|\n"
    for row in klines[-20:]:
        date, op, hi, lo, cl, vol, pct, to = row
        vol_str = f"{vol:,.0f}" if vol else "0"
        pct_str = f"{pct:+.2f}" if pct is not None else "0.00"
        kline_table += f"| {date} | {op:.2f} | {hi:.2f} | {lo:.2f} | {cl:.2f} | {vol_str} | {pct_str} |\n"

    # ── 拼成完整提示词 ──
    prompt = f"""你是一位经验丰富的A股技术分析师。请认真阅读以下数据，对 {name}（{code}）进行专业的技术分析。

{indicators}

{kline_table}

请从以下5个维度给出你的分析。要求**每条结论都引用具体数据**，不要泛泛而谈：

1. **趋势判断**
   - 当前处于什么趋势（上升/下降/震荡）？
   - 用均线排列、价格位置来佐证你的判断。

2. **关键价位**
   - 支撑位在哪里？（最近的均线或前期低点）
   - 阻力位在哪里？（最近的均线或前期高点）

3. **买卖信号**
   - 当前有哪些买入信号？哪些卖出信号？
   - MACD、RSI、KDJ 之间有没有共振或背离？

4. **风险提示**
   - 如果现在持有，最大的风险是什么？
   - 如果打算买入，需要警惕什么？

5. **操作建议**
   - 给出短线（1-5天）和中线（1-4周）两条具体建议。
   - 说明你建议的止损位和止盈位。

请用中文回答，结论先行，简洁专业。"""
    
    return prompt


# ──────────────────────────────────────────────
# 3. 调用 Ollama API（流式输出，能看到模型一个字一个字地写）
# ──────────────────────────────────────────────
def call_ollama(prompt, model=DEFAULT_MODEL):
    """把提示词发给本地 Ollama 模型，实时打印回复"""

    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": {
            "temperature": 0.3,   # 低温度 → 输出更稳定、更确定
            "num_predict": 2048,  # 最多生成 2048 个 token
        }
    }).encode("utf-8")

    req = urllib.request.Request(OLLAMA_URL, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")

    print(f"\n{'='*60}")
    print(f"  🤖 {model} 正在分析...")
    print(f"{'='*60}\n")

    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            full_text = []
            for line in resp:
                line = line.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    text = chunk.get("response", "")
                    if text:
                        print(text, end="", flush=True)
                        full_text.append(text)
                    if chunk.get("done"):
                        break
                except json.JSONDecodeError:
                    continue
            print("\n")
            return "".join(full_text)

    except urllib.error.URLError as e:
        print(f"\n[错误] 无法连接 Ollama: {e}")
        print("请确保 Ollama 正在运行（终端运行: ollama serve）")
        sys.exit(1)
    except Exception as e:
        print(f"\n[错误] {e}")
        sys.exit(1)


# ──────────────────────────────────────────────
# 4. 主入口
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Ollama 股票深度分析",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python ollama_analyze.py 600519                  # Qwen 分析茅台
  python ollama_analyze.py 600105 --deep           # 深度分析永鼎股份
  python ollama_analyze.py 600519 --model deepseek-r1:14b  # 用 DeepSeek
        """
    )
    parser.add_argument("code", help="股票代码，如 600519")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"模型名称（默认: {DEFAULT_MODEL}）")
    parser.add_argument("--deep", action="store_true",
                        help="包含长期指标（60日/250日区间位置）")
    parser.add_argument("--days", type=int, default=60,
                        help="读取最近多少天的K线（默认60）")

    args = parser.parse_args()

    # Step 1: 获取技术分析报告
    print(f"📊 正在读取 {args.code} 的技术指标...")
    conn = get_conn()
    report = analyze_stock(conn, args.code, deep=args.deep)
    conn.close()

    if report is None:
        print(f"[错误] 没有找到股票 {args.code} 的数据，请先下载数据")
        sys.exit(1)

    print(f"   ✓ 股票: {report.get('股票名称', args.code)}")
    print(f"   ✓ 最新价: {report.get('最新价')}")
    print(f"   ✓ 涨跌幅: {report.get('涨跌幅%')}%")

    # Step 2: 获取最近K线
    klines = get_recent_klines(args.code, args.days)
    if not klines:
        print(f"[错误] 数据库中没有 {args.code} 的K线数据")
        sys.exit(1)

    print(f"   ✓ 读取了 {len(klines)} 根K线")

    # Step 3: 构建提示词
    prompt = build_prompt(args.code, report, klines)

    # Step 4: 喂给 Ollama
    call_ollama(prompt, args.model)


if __name__ == "__main__":
    main()
