#!/usr/bin/env python3
"""
股票分析 MCP Server — 零外部依赖，纯 Python 标准库实现 MCP 协议。
让 WorkBuddy 对话直接调用本地股票数据和分析能力。

工具：
  stock_search          - 搜索股票（名称/代码模糊匹配）
  stock_analyze         - 单只股票技术分析
  stock_batch_scan      - 批量扫描
  stock_market_overview - 市场概况
  stock_ask_ollama      - 自然语言提问，Ollama 回答

用法：在 ~/.workbuddy/mcp.json 配置后，WorkBuddy 可直接调用。
"""

import sys
import os
import json
import sqlite3
import urllib.request
from typing import Optional, Union

# ─── 路径配置 ──────────────────────────────────────────
STOCK_TOOLS = os.path.expanduser("~/stock-tools")
DB_PATH = os.path.expanduser("~/stock-data/all_stocks.db")
OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "qwen2.5:14b-ctx"

# 将 analyze 模块路径加入
sys.path.insert(0, STOCK_TOOLS)
from analyze import get_conn, analyze_stock
from analyze import calc_obv, obv_features


# ─── 工具函数 ──────────────────────────────────────────

def _search_stock(keyword: str, limit: int = 15) -> list:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT code, name, market, industry FROM stock_list "
        "WHERE code LIKE ? OR name LIKE ? LIMIT ?",
        (f"%{keyword}%", f"%{keyword}%", limit)
    )
    rows = [{"code": r[0], "name": r[1], "market": r[2], "industry": r[3]} for r in c.fetchall()]
    conn.close()
    return rows


def _market_overview(days: int = 5) -> dict:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("SELECT COUNT(DISTINCT code) FROM stocks")
    total = c.fetchone()[0]

    c.execute("""
        SELECT code, 
               MAX(CASE WHEN rn=1 THEN close END) as latest,
               MAX(CASE WHEN rn=? THEN close END) as prev
        FROM (
            SELECT code, close,
                   ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) as rn
            FROM stocks
        )
        WHERE rn IN (1, ?)
        GROUP BY code
        HAVING latest IS NOT NULL AND prev IS NOT NULL
    """, (days, days))

    up, down, flat = 0, 0, 0
    detail = []
    for code, latest, prev in c.fetchall():
        if prev and prev > 0:
            pct = round((latest / prev - 1) * 100, 2)
            if pct > 0:
                up += 1
            elif pct < 0:
                down += 1
            else:
                flat += 1
            detail.append({"code": code, "latest": latest, "pct": pct})

    detail.sort(key=lambda x: x["pct"], reverse=True)
    top_gainers = detail[:10]
    top_losers = sorted(detail, key=lambda x: x["pct"])[:10]

    for lst in [top_gainers, top_losers]:
        for s in lst:
            c.execute("SELECT name FROM stock_list WHERE code=?", (s["code"],))
            row = c.fetchone()
            s["name"] = row[0] if row else "未知"

    conn.close()
    return {
        "total": total,
        "analyzed": len(detail),
        "up": up, "down": down, "flat": flat,
        "up_ratio": round(up / len(detail) * 100, 1) if detail else 0,
        "top_gainers": top_gainers,
        "top_losers": top_losers,
    }


def _call_ollama(prompt: str, model: str = DEFAULT_MODEL) -> str:
    import traceback, datetime
    log = open("/tmp/stock_mcp_ollama.log", "a")
    try:
        log.write(f"\n[{datetime.datetime.now()}] 调用Ollama model={model}, prompt长度={len(prompt)}\n")
        data = json.dumps({
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.3}
        }).encode("utf-8")
        req = urllib.request.Request(OLLAMA_URL, data=data,
                                     headers={"Content-Type": "application/json"})
        log.write("发送请求...\n"); log.flush()
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            result = body.get("response", "[模型返回空]")
            log.write(f"Ollama返回: {result[:100]}...\n"); log.flush()
            log.close()
            return result
    except urllib.error.URLError as e:
        log.write(f"URLError: {e}\n"); log.close()
        return "[错误] 无法连接 Ollama，请确认 Ollama 正在运行"
    except Exception as e:
        log.write(f"Exception: {type(e).__name__}: {str(e)}\n")
        log.write(traceback.format_exc() + "\n"); log.close()
        return f"[错误] {type(e).__name__}: {str(e)}"


def _format_report(report: dict) -> str:
    lines = [f"# {report.get('股票名称', '未知')}（{report.get('代码', '')}）技术分析", ""]

    lines.append("## 价格信息")
    lines.append(f"- 最新价：**{report.get('最新价', 'N/A')}**")
    lines.append(f"- 涨跌幅：**{report.get('涨跌幅%', 'N/A')}%**")
    lines.append(f"- 振幅：{report.get('振幅%', 'N/A')}%")
    lines.append(f"- 分析周期：{report.get('分析周期', 'N/A')}")
    lines.append("")

    lines.append("## 均线形态")
    lines.append(f"- 判断：**{report.get('均线形态', 'N/A')}**")
    for k, v in report.items():
        if k.startswith("MA"):
            lines.append(f"- {k}：{v}")
    lines.append("")

    lines.append("## MACD")
    lines.append(f"- 判断：**{report.get('MACD判断', 'N/A')}**")
    lines.append(f"- 详细：{report.get('MACD详细', 'N/A')}")
    lines.append("")

    lines.append("## RSI")
    lines.append(f"- RSI(6)：{report.get('RSI6', 'N/A')}")
    lines.append(f"- RSI(14)：{report.get('RSI14', 'N/A')}")
    lines.append(f"- RSI(24)：{report.get('RSI24', 'N/A')}")
    lines.append(f"- 判断：**{report.get('RSI判断', 'N/A')}**")
    lines.append("")

    lines.append("## KDJ")
    lines.append(f"- K：{report.get('K值', 'N/A')}，D：{report.get('D值', 'N/A')}，J：{report.get('J值', 'N/A')}")
    lines.append(f"- 判断：**{report.get('KDJ判断', 'N/A')}**")
    lines.append("")

    if '成交量判断' in report:
        lines.append("## 成交量")
        lines.append(f"- 判断：**{report.get('成交量判断', 'N/A')}**")
        lines.append("")

    if '60日最高' in report:
        lines.append("## 深度指标")
        pos60 = report.get('60日位置%', 'N/A')
        dist60 = report.get('距60日最高%', 'N/A')
        pos250 = report.get('250日位置%', 'N/A')
        dist250 = report.get('距250日最高%', 'N/A')
        lines.append(f"- 60日位置：{pos60}%（距最高{dist60}%）")
        lines.append(f"- 250日位置：{pos250}%（距最高{dist250}%）")
        lines.append("")

    return "\n".join(lines)


# ─── 工具处理器 ────────────────────────────────────────

def tool_search(args: dict) -> str:
    kw = args.get("keyword", "")
    limit = args.get("limit", 15)
    results = _search_stock(kw, limit)
    if not results:
        return f"未找到匹配「{kw}」的股票"

    lines = [f"# 搜索「{kw}」结果（共{len(results)}条）", ""]
    lines.append("| 代码 | 名称 | 市场 | 行业 |")
    lines.append("|------|------|------|------|")
    for s in results:
        lines.append(f"| {s['code']} | {s.get('name','N/A')} | {s.get('market','N/A')} | {s.get('industry','-') or '-'} |")
    return "\n".join(lines)


def tool_analyze(args: dict) -> str:
    code = args.get("code", "")
    deep = args.get("deep", False)
    use_ai = args.get("use_ollama", False)
    model = args.get("ollama_model", DEFAULT_MODEL)

    conn = get_conn()
    try:
        report = analyze_stock(conn, code, deep=deep)
    except Exception as e:
        conn.close()
        return f"[错误] 分析失败: {str(e)}"
    conn.close()

    if not report:
        return f"未找到股票代码 {code} 的数据"

    md = _format_report(report)

    if use_ai:
        prompt = f"""你是A股技术分析师。请基于以下指标给出投资建议。

{md}

从趋势判断、支撑阻力、买卖信号、风险提示、操作建议五个维度分析，每条引用具体数据，用中文。"""
        md += "\n\n---\n\n## AI 解读\n\n" + _call_ollama(prompt, model)

    return md


def tool_batch_scan(args: dict) -> str:
    gain = args.get("gain_pct", 20)
    days = args.get("days", 30)
    pre_window = args.get("pre_window", 20)
    top_n = args.get("top_n", 20)
    check_obv = args.get("check_obv", True)
    use_ai = args.get("use_ollama", False)

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT DISTINCT code FROM stocks")
    all_codes = [r[0] for r in c.fetchall()]

    if not all_codes:
        conn.close()
        return "数据库为空，请先下载数据"

    gainers = []
    for code in all_codes:
        c.execute("SELECT date, close, volume FROM stocks WHERE code=? ORDER BY date", (code,))
        rows = c.fetchall()
        if len(rows) < days + pre_window + 5:
            continue

        closes = [r[1] for r in rows]
        volumes = [r[2] for r in rows]
        if not closes[-1] or not closes[-(days+1)] or closes[-(days+1)] <= 0:
            continue

        g = (closes[-1] / closes[-(days+1)] - 1) * 100
        if g < gain:
            continue

        obv_feat = None
        if check_obv:
            pre_start = max(0, len(rows) - days - 1 - pre_window)
            pre_closes = closes[pre_start:len(rows)-days-1]
            pre_volumes = volumes[pre_start:len(rows)-days-1]
            if len(pre_closes) >= pre_window:
                obv_feat = obv_features(pre_closes, pre_volumes, window=pre_window)

        c.execute("SELECT name FROM stock_list WHERE code=?", (code,))
        nr = c.fetchone()
        gainers.append({
            "code": code,
            "name": nr[0] if nr else "未知",
            "gain": round(g, 2),
            "price": closes[-1],
            "obv": obv_feat
        })

    conn.close()
    gainers.sort(key=lambda x: x["gain"], reverse=True)
    top = gainers[:top_n]

    # 统计 OBV 特征
    stats = {"上升趋势": 0, "下降趋势": 0, "横盘": 0, "吸筹背离": 0, "OBV突破": 0, "放量": 0}
    valid = 0
    for g in top:
        o = g.get("obv")
        if not o:
            continue
        valid += 1
        if o["obv_trend"] == "上升": stats["上升趋势"] += 1
        elif o["obv_trend"] == "下降": stats["下降趋势"] += 1
        else: stats["横盘"] += 1
        if o.get("divergence") and "吸筹" in o.get("divergence_type", ""):
            stats["吸筹背离"] += 1
        if o.get("obv_breakout"): stats["OBV突破"] += 1
        if o.get("vol_expand"): stats["放量"] += 1

    lines = [f"# 批量扫描：近{days}天涨幅 ≥ {gain}%", f"共扫描 {len(all_codes)} 只，{len(gainers)} 只达标，展示前{len(top)}只", ""]

    if valid:
        lines.append("## 启动前 OBV 特征统计")
        lines.append(f"| 特征 | 数量 | 占比 |")
        lines.append("|------|------|------|")
        for k in ["上升趋势", "下降趋势", "横盘", "吸筹背离", "OBV突破", "放量"]:
            lines.append(f"| {k} | {stats[k]} | {round(stats[k]/valid*100)}% |")
        lines.append("")

    lines.append("## 涨幅排名")
    lines.append("| 排名 | 代码 | 名称 | 涨幅% | 最新价 | OBV趋势 | 背离 | 放量 |")
    lines.append("|------|------|------|--------|--------|---------|------|------|")
    for i, g in enumerate(top, 1):
        o = g.get("obv")
        trend = o["obv_trend"] if o else "-"
        div = o["divergence_type"] if o and o.get("divergence") else "-"
        vol = "是" if o and o.get("vol_expand") else "-"
        lines.append(f"| {i} | {g['code']} | {g['name']} | {g['gain']}% | {g['price']} | {trend} | {div} | {vol} |")

    result = "\n".join(lines)
    if use_ai:
        result += "\n\n---\n\n## AI 综合分析\n\n" + _call_ollama(
            f"你是量化分析师。以下是A股批量扫描结果，请分析牛股启动前共性特征，给出选股建议。用中文。\n\n{result}")

    return result


def tool_market_overview(args: dict) -> str:
    days = args.get("days", 5)
    d = _market_overview(days)

    lines = [f"# 市场概况（近{days}天）", "",
             f"- 有数据：{d['analyzed']} 只",
             f"- 上涨：**{d['up']}**（{d['up_ratio']}%）",
             f"- 下跌：{d['down']}，平盘：{d['flat']}", ""]

    lines.append("## 涨幅前10")
    lines.append("| # | 代码 | 名称 | 涨幅% | 最新价 |")
    lines.append("|---|------|------|--------|--------|")
    for i, s in enumerate(d["top_gainers"], 1):
        lines.append(f"| {i} | {s['code']} | {s['name']} | {s['pct']}% | {s['latest']} |")

    lines.append("")
    lines.append("## 跌幅前10")
    lines.append("| # | 代码 | 名称 | 跌幅% | 最新价 |")
    lines.append("|---|------|------|--------|--------|")
    for i, s in enumerate(d["top_losers"], 1):
        lines.append(f"| {i} | {s['code']} | {s['name']} | {s['pct']}% | {s['latest']} |")

    return "\n".join(lines)


def tool_ask_ollama(args: dict) -> str:
    question = args.get("question", "")
    model = args.get("model", DEFAULT_MODEL)

    overview = _market_overview(5)
    ctx = f"当前市场（近5天）：{overview['analyzed']}只有数据，上涨{overview['up']}只（{overview['up_ratio']}%）。涨幅前5："
    for s in overview["top_gainers"][:5]:
        ctx += f" {s['code']} {s['name']} {s['pct']}%;"

    prompt = f"你是A股分析师。\n{ctx}\n\n用户提问：{question}\n请基于数据回答，不知道就说不知道。中文。"
    ans = _call_ollama(prompt, model)
    return f"## 问题\n{question}\n\n## 回答\n\n{ans}"


# ─── MCP 协议处理 ──────────────────────────────────────

TOOLS = [
    {
        "name": "stock_search",
        "description": "搜索股票代码或名称，返回匹配的股票列表。输入如'600519'或'茅台'。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "搜索关键词（代码或名称）"},
                "limit": {"type": "integer", "description": "返回数量上限", "default": 15}
            },
            "required": ["keyword"]
        }
    },
    {
        "name": "stock_analyze",
        "description": "对单只股票进行完整技术分析（均线/MACD/RSI/KDJ），可选AI解读。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "股票代码，如'600519'"},
                "deep": {"type": "boolean", "description": "是否深度分析", "default": False},
                "use_ollama": {"type": "boolean", "description": "是否调用Ollama AI解读", "default": False},
                "ollama_model": {"type": "string", "description": "Ollama模型名称", "default": "qwen2.5:14b-ctx"}
            },
            "required": ["code"]
        }
    },
    {
        "name": "stock_batch_scan",
        "description": "批量扫描全市场，按涨幅筛选牛股，分析启动前OBV/成交量特征。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "gain_pct": {"type": "number", "description": "涨幅阈值(%)", "default": 20},
                "days": {"type": "integer", "description": "统计天数", "default": 30},
                "pre_window": {"type": "integer", "description": "启动前分析窗口", "default": 20},
                "top_n": {"type": "integer", "description": "返回前N只", "default": 20},
                "check_obv": {"type": "boolean", "description": "是否分析OBV", "default": True},
                "use_ollama": {"type": "boolean", "description": "是否AI解读", "default": False}
            }
        }
    },
    {
        "name": "stock_market_overview",
        "description": "获取市场概况：涨跌统计、涨幅榜、跌幅榜。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "统计天数", "default": 5}
            }
        }
    },
    {
        "name": "stock_ask_ollama",
        "description": "自然语言提问，Ollama自动读市场数据回答。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "自然语言问题"},
                "model": {"type": "string", "description": "Ollama模型", "default": "qwen2.5:14b-ctx"}
            },
            "required": ["question"]
        }
    }
]


TOOL_HANDLERS = {
    "stock_search": tool_search,
    "stock_analyze": tool_analyze,
    "stock_batch_scan": tool_batch_scan,
    "stock_market_overview": tool_market_overview,
    "stock_ask_ollama": tool_ask_ollama,
}

SERVER_INFO = {
    "name": "stock_mcp",
    "version": "1.0.0"
}


def handle_request(req: dict) -> Optional[dict]:
    """处理单个 JSON-RPC 请求，返回响应或 None（通知）"""
    method = req.get("method", "")
    req_id = req.get("id")

    def reply(result):
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def error(code, msg):
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": msg}}

    if method == "initialize":
        return reply({
            "protocolVersion": "2024-11-05",
            "serverInfo": SERVER_INFO,
            "capabilities": {"tools": {}}
        })

    if method == "notifications/initialized":
        return None  # 通知，无需响应

    if method == "tools/list":
        return reply({"tools": TOOLS})

    if method == "tools/call":
        params = req.get("params", {})
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            return error(-32601, f"未知工具: {tool_name}")

        try:
            result_text = handler(arguments)
            return reply({
                "content": [{"type": "text", "text": result_text}]
            })
        except Exception as e:
            return error(-32603, f"工具执行错误: {str(e)}")

    if method == "ping":
        return reply({})

    return error(-32601, f"未知方法: {method}")


def main():
    """主循环：从 stdin 读取 JSON-RPC，写入 stdout"""
    buf = ""
    while True:
        try:
            line = sys.stdin.readline()
        except KeyboardInterrupt:
            break
        except EOFError:
            break

        if not line:
            break  # stdin 关闭

        buf += line
        if not line.strip():
            continue

        try:
            req = json.loads(buf)
            buf = ""
        except json.JSONDecodeError:
            continue  # 继续累积，可能是多行 JSON

        resp = handle_request(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
