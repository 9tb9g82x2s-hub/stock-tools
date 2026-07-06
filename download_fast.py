#!/usr/bin/python3
"""
A股全市场历史数据下载器（快速版）
- 新浪数据源（不走东方财富，永不封禁）
- 5线程并发，约12分钟下完全市场5500只
- 强制IPv4

用法:
    /usr/bin/python3 ~/stock-tools/download_fast.py              # 全量下载
    /usr/bin/python3 ~/stock-tools/download_fast.py --update     # 增量更新
    /usr/bin/python3 ~/stock-tools/download_fast.py --codes 600519,000001
"""

import socket as _socket
_orig_getaddrinfo = _socket.getaddrinfo
def _ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, _socket.AF_INET, type, proto, flags)
_socket.getaddrinfo = _ipv4_only

import sqlite3, time, os, sys, argparse, threading, warnings
warnings.filterwarnings("ignore")
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

DB_PATH = os.path.expanduser("~/stock-data/all_stocks.db")
MAX_WORKERS = 5
RETRY = 2
CACHE_LIMIT = 50000  # 缓存多少条记录后写DB


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS stocks (
        code TEXT NOT NULL, name TEXT NOT NULL, date TEXT NOT NULL,
        open REAL, high REAL, low REAL, close REAL,
        volume REAL, amount REAL, amplitude REAL, pct_change REAL, change REAL, turnover REAL,
        PRIMARY KEY (code, date))""")
    c.execute("""CREATE TABLE IF NOT EXISTS stock_list (
        code TEXT PRIMARY KEY, name TEXT NOT NULL,
        industry TEXT, market TEXT, list_date TEXT, updated_at TEXT)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_date ON stocks(date)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_code ON stocks(code)")
    conn.commit()
    conn.close()


def get_stock_list(conn):
    c = conn.cursor()
    c.execute("SELECT code, name FROM stock_list ORDER BY code")
    rows = c.fetchall()
    if rows and len(rows) > 100:
        print(f"[1/3] 本地已有股票列表: {len(rows)} 只")
        return [{"code": r[0], "name": r[1]} for r in rows]

    print("[1/3] 联网获取股票列表...")
    import akshare as ak
    df = ak.stock_info_a_code_name()
    stocks = []
    for _, row in df.iterrows():
        code = str(row["code"]).strip()
        name = str(row["name"]).strip()
        if code and name and "退" not in name:
            stocks.append({"code": code, "name": name})
    print(f"  共 {len(stocks)} 只")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.executemany(
        "INSERT OR REPLACE INTO stock_list (code,name,updated_at) VALUES (?,?,?)",
        [(s["code"], s["name"], now) for s in stocks])
    conn.commit()
    return stocks


def download_one(code, name, start_date, end_date):
    """下载单只（线程安全）"""
    import akshare as ak
    prefix = "sh" if code.startswith("6") else "sz"
    symbol = f"{prefix}{code}"
    beg = start_date.replace("-", "")
    end = end_date.replace("-", "")

    for i in range(RETRY + 1):
        try:
            df = ak.stock_zh_a_daily(symbol=symbol, start_date=beg, end_date=end, adjust="qfq")
            if df is None or df.empty:
                return code, name, []
            records = []
            for _, row in df.iterrows():
                try:
                    date_val = str(row.get("date", ""))[:10]
                    if not date_val or date_val == "nan":
                        continue
                    records.append({
                        "code": code, "name": name, "date": date_val,
                        "open": float(row.get("open",0) or 0),
                        "close": float(row.get("close",0) or 0),
                        "high": float(row.get("high",0) or 0),
                        "low": float(row.get("low",0) or 0),
                        "volume": float(row.get("volume",0) or 0),
                        "amount": float(row.get("amount",0) or 0),
                        "amplitude": 0.0, "pct_change": 0.0, "change": 0.0,
                        "turnover": float(row.get("turnover",0) or 0),
                    })
                except (ValueError, TypeError):
                    continue
            return code, name, records
        except Exception:
            if i < RETRY:
                time.sleep(0.5)
    return code, name, None  # 失败


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--codes", type=str)
    parser.add_argument("--years", type=int, default=3)
    parser.add_argument("--from", dest="from_date", type=str)
    parser.add_argument("--to", dest="to_date", type=str)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS)
    args = parser.parse_args()

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    init_db()
    conn = sqlite3.connect(DB_PATH)

    def fmt(d): return d.replace("-", "") if d else d
    end_date   = fmt(args.to_date)   or datetime.now().strftime("%Y%m%d")
    start_date = fmt(args.from_date) or (datetime.now() - timedelta(days=365*args.years)).strftime("%Y%m%d")
    print(f"数据范围: {start_date} ~ {end_date}")

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",")]
        c = conn.cursor()
        name_map = {r[0]: r[1] for r in c.execute("SELECT code,name FROM stock_list")}
        stocks = [{"code": cd, "name": name_map.get(cd, cd)} for cd in codes]
        print(f"[1/3] 指定股票: {len(codes)} 只")
    else:
        stocks = get_stock_list(conn)

    if args.update:
        c = conn.cursor()
        c.execute("SELECT MAX(date) FROM stocks")
        row = c.fetchone()
        if row and row[0]:
            start_date = (datetime.strptime(row[0][:10], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y%m%d")
            print(f"[增量] 从 {start_date} 开始\n")

    skip_set = set()
    if args.skip_existing:
        c = conn.cursor()
        c.execute("SELECT DISTINCT code FROM stocks")
        skip_set = {r[0] for r in c.fetchall()}
        print(f"  跳过 {len(skip_set)} 只已有数据\n")

    todo = [s for s in stocks if s["code"] not in skip_set]
    total = len(todo)
    print(f"[2/3] 并发:{args.workers}线程 | 待下载:{total}只\n")

    lock = threading.Lock()
    stats = {"ok": 0, "skip": 0, "err": 0, "done": 0}
    batch = []
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(download_one, s["code"], s["name"], start_date, end_date): s for s in todo}
        for fut in as_completed(futs):
            code, name, records = fut.result()
            with lock:
                stats["done"] += 1
                done = stats["done"]
                ok = stats["ok"]
                err = stats["err"]
                elapsed = time.time() - start_time
                speed = done / elapsed if elapsed > 0 else 0
                eta = (total - done) / speed / 60 if speed > 0 else 0

                if records is None:
                    err += 1; stats["err"] = err
                elif records:
                    batch.extend(records)
                    ok += 1; stats["ok"] = ok
                else:
                    stats["skip"] += 1

                # 批量写DB
                if len(batch) >= CACHE_LIMIT:
                    c = conn.cursor()
                    c.executemany("""INSERT OR REPLACE INTO stocks
                        (code,name,date,open,high,low,close,volume,amount,amplitude,pct_change,change,turnover)
                        VALUES (:code,:name,:date,:open,:high,:low,:close,:volume,:amount,:amplitude,:pct_change,:change,:turnover)""",
                        batch)
                    conn.commit()
                    batch = []

                bar = f"\r  [{done}/{total}] ✅{ok} ❌{err} | {elapsed:.0f}s | ~{eta:.1f}分钟剩余 | {code} {name[:6]:6s}"
                print(bar + " " * 10, end="", flush=True)

    # 最后一批
    if batch:
        c = conn.cursor()
        c.executemany("""INSERT OR REPLACE INTO stocks
            (code,name,date,open,high,low,close,volume,amount,amplitude,pct_change,change,turnover)
            VALUES (:code,:name,:date,:open,:high,:low,:close,:volume,:amount,:amplitude,:pct_change,:change,:turnover)""",
            batch)
        conn.commit()

    total_t = time.time() - start_time
    print(f"\n\n[3/3] ✅{stats['ok']} ⏭{stats['skip']} ❌{stats['err']} | {total_t:.0f}s ({total_t/60:.1f}分钟)")

    c = conn.cursor()
    c.execute("SELECT COUNT(DISTINCT code) FROM stocks")
    print(f"  总股票数: {c.fetchone()[0]}")
    c.execute("SELECT COUNT(*) FROM stocks")
    print(f"  总记录数: {c.fetchone()[0]:,}")
    c.execute("SELECT MIN(date), MAX(date) FROM stocks")
    r = c.fetchone()
    print(f"  日期范围: {r[0]} ~ {r[1]}")
    conn.close()


if __name__ == "__main__":
    main()
