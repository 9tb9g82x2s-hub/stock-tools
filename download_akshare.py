#!/usr/bin/python3
"""
A股全市场历史数据下载器（AKShare版）
- 强制 IPv4，绕过 Mac LibreSSL/IPv6 问题
- 使用 AKShare 官方库，稳定不被封
- 支持全量下载、增量更新、指定股票

用法:
    /usr/bin/python3 ~/stock-tools/download_akshare.py              # 全量下载（约30-60分钟）
    /usr/bin/python3 ~/stock-tools/download_akshare.py --update     # 仅补全最新数据
    /usr/bin/python3 ~/stock-tools/download_akshare.py --codes 600519,000001
    /usr/bin/python3 ~/stock-tools/download_akshare.py --years 1    # 只下1年
"""

# ─── 强制 IPv4（必须在所有 import 之前）─────────────────────────────────────
import socket as _socket
_orig_getaddrinfo = _socket.getaddrinfo
def _ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, _socket.AF_INET, type, proto, flags)
_socket.getaddrinfo = _ipv4_only
# ────────────────────────────────────────────────────────────────────────────

import sqlite3
import time
import os
import sys
import argparse
import warnings
warnings.filterwarnings("ignore")   # 屏蔽 LibreSSL 警告

from datetime import datetime, timedelta

DB_PATH = os.path.expanduser("~/stock-data/all_stocks.db")
DELAY   = 0.3   # API 调用间隔（秒），AKShare 建议 ≥ 0.3
BATCH_SAVE = 50  # 每下载多少只股票写一次数据库


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS stocks (
        code TEXT NOT NULL,
        name TEXT NOT NULL,
        date TEXT NOT NULL,
        open REAL, high REAL, low REAL, close REAL,
        volume REAL, amount REAL,
        amplitude REAL, pct_change REAL, change REAL, turnover REAL,
        PRIMARY KEY (code, date)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS stock_list (
        code TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        industry TEXT, market TEXT,
        list_date TEXT, updated_at TEXT
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_date ON stocks(date)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_code ON stocks(code)")
    conn.commit()
    conn.close()


def get_stock_list(conn):
    """优先本地读，否则联网获取"""
    c = conn.cursor()
    c.execute("SELECT code, name FROM stock_list ORDER BY code")
    rows = c.fetchall()
    if rows and len(rows) > 100:
        print(f"[1/3] 从本地数据库读取股票列表: {len(rows)} 只")
        return [{"code": r[0], "name": r[1]} for r in rows]

    print("[1/3] 联网获取A股股票列表（AKShare）...")
    import akshare as ak
    df = ak.stock_info_a_code_name()
    stocks = []
    for _, row in df.iterrows():
        code = str(row["code"]).strip()
        name = str(row["name"]).strip()
        if code and name and "退" not in name:
            stocks.append({"code": code, "name": name})

    print(f"  共获取 {len(stocks)} 只")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.executemany(
        "INSERT OR REPLACE INTO stock_list (code, name, updated_at) VALUES (?,?,?)",
        [(s["code"], s["name"], now) for s in stocks]
    )
    conn.commit()
    return stocks


def download_one_akshare(code, name, start_date, end_date):
    """
    用 AKShare 新浪数据源下载单只股票前复权日K数据
    新浪数据源不走东方财富API，不受封禁影响
    返回 records 列表，失败返回 None
    """
    import akshare as ak

    # 新浪接口代码格式：sh600519 / sz000002
    prefix = "sh" if code.startswith("6") else "sz"
    symbol = f"{prefix}{code}"

    # 日期格式 YYYYMMDD
    beg = start_date.replace("-", "")
    end = end_date.replace("-", "")

    for attempt in range(3):
        try:
            df = ak.stock_zh_a_daily(
                symbol=symbol,
                start_date=beg,
                end_date=end,
                adjust="qfq"
            )
            if df is None or df.empty:
                return []

            records = []
            for _, row in df.iterrows():
                try:
                    date_val = str(row.get("date", ""))[:10]
                    if not date_val or date_val == "nan":
                        continue
                    records.append({
                        "code": code,
                        "name": name,
                        "date": date_val,
                        "open":       float(row.get("open",   0) or 0),
                        "close":      float(row.get("close",  0) or 0),
                        "high":       float(row.get("high",   0) or 0),
                        "low":        float(row.get("low",    0) or 0),
                        "volume":     float(row.get("volume", 0) or 0),
                        "amount":     float(row.get("amount", 0) or 0),
                        "amplitude":  0.0,
                        "pct_change": 0.0,
                        "change":     0.0,
                        "turnover":   float(row.get("turnover", 0) or 0),
                    })
                except (ValueError, TypeError):
                    continue
            return records

        except Exception:
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
            else:
                return None   # 三次失败
    return None


def save_batch(conn, records):
    if not records:
        return
    c = conn.cursor()
    c.executemany("""INSERT OR REPLACE INTO stocks
        (code,name,date,open,high,low,close,volume,amount,amplitude,pct_change,change,turnover)
        VALUES (:code,:name,:date,:open,:high,:low,:close,:volume,:amount,:amplitude,:pct_change,:change,:turnover)""",
        records)
    conn.commit()


def main():
    parser = argparse.ArgumentParser(description="A股全市场数据下载器（AKShare版）")
    parser.add_argument("--update",    action="store_true", help="增量更新")
    parser.add_argument("--codes",     type=str,  help="指定股票代码，逗号分隔")
    parser.add_argument("--years",     type=int,  default=3, help="下载年数（默认3年）")
    parser.add_argument("--from",      dest="from_date", type=str, help="起始日期 YYYYMMDD 或 YYYY-MM-DD")
    parser.add_argument("--to",        dest="to_date",   type=str, help="结束日期 YYYYMMDD 或 YYYY-MM-DD")
    parser.add_argument("--delay",     type=float, default=DELAY, help=f"API间隔（秒，默认{DELAY}）")
    parser.add_argument("--skip-existing", action="store_true", help="跳过已有数据的股票（增量补全用）")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    init_db()
    conn = sqlite3.connect(DB_PATH)

    # 日期格式统一为 YYYYMMDD
    def fmt(d):
        return d.replace("-", "") if d else d

    end_date   = fmt(args.to_date)   or datetime.now().strftime("%Y%m%d")
    start_date = fmt(args.from_date) or (datetime.now() - timedelta(days=365 * args.years)).strftime("%Y%m%d")

    print(f"数据范围: {start_date} ~ {end_date}\n")

    # 获取股票列表
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",")]
        print(f"[1/3] 指定股票: {len(codes)} 只")
        # 尝试从本地库补全名称
        c = conn.cursor()
        name_map = {}
        for row in c.execute("SELECT code, name FROM stock_list"):
            name_map[row[0]] = row[1]
        stocks = [{"code": cd, "name": name_map.get(cd, cd)} for cd in codes]
    else:
        stocks = get_stock_list(conn)

    # 增量更新：调整起始日期
    if args.update:
        c = conn.cursor()
        c.execute("SELECT MAX(date) FROM stocks")
        row = c.fetchone()
        if row and row[0]:
            start_date = (datetime.strptime(row[0][:10], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y%m%d")
            print(f"[增量更新] 从 {start_date} 开始\n")

    # 跳过已有数据的股票
    skip_set = set()
    if args.skip_existing:
        c = conn.cursor()
        c.execute("SELECT DISTINCT code FROM stocks")
        skip_set = {r[0] for r in c.fetchall()}
        print(f"  已有数据的股票数: {len(skip_set)} 只，将跳过")

    total       = len(stocks)
    batch       = []
    success_cnt = 0
    skip_cnt    = 0
    error_cnt   = 0
    start_time  = time.time()

    print(f"[2/3] 开始下载（间隔{args.delay}s/股，共{total}只）...\n")

    for i, stock in enumerate(stocks):
        code = stock["code"]
        name = stock.get("name", code)

        if code in skip_set:
            skip_cnt += 1
            continue

        elapsed = time.time() - start_time
        done    = i + 1
        speed   = done / elapsed if elapsed > 0 else 1
        eta     = (total - done) / speed if speed > 0 else 0
        print(f"\r  [{done}/{total}] {code} {name[:6]} | ✅{success_cnt} ⏭{skip_cnt} ❌{error_cnt} | {elapsed:.0f}s | ~{eta/60:.1f}分钟剩余  ",
              end="", flush=True)

        records = download_one_akshare(code, name, start_date, end_date)

        if records is None:
            error_cnt += 1
        elif len(records) > 0:
            batch.extend(records)
            success_cnt += 1
        else:
            skip_cnt += 1   # 空数据（可能退市/停牌）

        # 批量写入数据库
        if len(batch) >= BATCH_SAVE * 200:
            save_batch(conn, batch)
            batch = []

        time.sleep(args.delay)

    # 最后一批
    if batch:
        save_batch(conn, batch)

    total_time = time.time() - start_time
    print(f"\n\n[3/3] 完成！✅成功:{success_cnt} ⏭跳过:{skip_cnt} ❌失败:{error_cnt} | 总耗时:{total_time:.0f}s ({total_time/60:.1f}分钟)")

    c = conn.cursor()
    c.execute("SELECT COUNT(DISTINCT code) FROM stocks")
    print(f"  股票数: {c.fetchone()[0]}")
    c.execute("SELECT COUNT(*) FROM stocks")
    print(f"  总记录: {c.fetchone()[0]:,}")
    c.execute("SELECT MIN(date), MAX(date) FROM stocks")
    r = c.fetchone()
    print(f"  日期范围: {r[0]} ~ {r[1]}")
    print(f"  数据库: {DB_PATH}")
    conn.close()


if __name__ == "__main__":
    main()
