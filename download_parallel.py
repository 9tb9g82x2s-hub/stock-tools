#!/usr/bin/python3
"""
A股全市场历史数据下载器（并发版）
使用 ThreadPoolExecutor 并发下载，速度是串行版的 6-8 倍
用法:
    python3 download_parallel.py              # 全量下载（约 3-5 分钟）
    python3 download_parallel.py --update     # 增量更新
    python3 download_parallel.py --codes 600519,000001   # 指定股票
"""

import sqlite3
import time
import os
import sys
import argparse
import subprocess
import json
import queue
import threading
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

DB_PATH = os.path.expanduser("~/stock-data/all_stocks.db")
MAX_WORKERS = 2        # 并发线程数（降低到2，避免被封）
API_DELAY = 0.5        # 线程内API调用间隔（增加到0.5秒）
CURL_TIMEOUT = 15
BATCH_SAVE = 200       # 每积累多少条写一次数据库
MAX_RETRY = 3


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
    """优先从本地数据库读取股票列表"""
    c = conn.cursor()
    c.execute("SELECT code, name FROM stock_list ORDER BY code")
    rows = c.fetchall()
    if rows and len(rows) > 100:
        print(f"[1/3] 从本地数据库读取股票列表: {len(rows)} 只")
        return [{"code": r[0], "name": r[1]} for r in rows]

    # 联网获取（分页）
    print("[1/3] 本地无列表，联网获取...")
    all_stocks = []
    page = 1
    while page <= 60:
        url = (f"https://push2.eastmoney.com/api/qt/clist/get"
               f"?pn={page}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3"
               f"&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
               f"&fields=f12,f14")
        cmd = ["curl", "-4", "-s", "--max-time", "15", "--retry", "2",
               "-H", "User-Agent: Mozilla/5.0",
               "-H", "Referer: https://quote.eastmoney.com", url]
        try:
            raw = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8", errors="replace")
            d = json.loads(raw)
            items = (d.get("data") or {}).get("diff") or []
            if not items:
                break
            for item in items:
                code = str(item.get("f12") or "").strip()
                name = str(item.get("f14") or "").strip()
                if code and name and "退" not in name:
                    all_stocks.append({"code": code, "name": name})
            if len(items) < 100:
                break
            page += 1
            time.sleep(0.1)
        except Exception as e:
            print(f"  [警告] 第{page}页失败: {e}")
            break

    if all_stocks:
        print(f"  共获取 {len(all_stocks)} 只")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for s in all_stocks:
            c.execute("INSERT OR REPLACE INTO stock_list (code, name, updated_at) VALUES (?, ?, ?)",
                      (s["code"], s["name"], now))
        conn.commit()
        return all_stocks

    print("[错误] 无法获取股票列表")
    sys.exit(1)


def code_to_secid(code):
    code = str(code).strip()
    return f"1.{code}" if code.startswith("6") else f"0.{code}"


def download_one(stock, start_date, end_date):
    """下载单只股票，供线程池调用"""
    code = stock["code"]
    name = stock.get("name", code)
    secid = code_to_secid(code)
    beg = start_date if len(start_date) == 8 else f"{start_date[:4]}{start_date[5:7]}{start_date[8:10]}"
    end = end_date   if len(end_date)   == 8 else f"{end_date[:4]}{end_date[5:7]}{end_date[8:10]}"

    url = (f"https://push2his.eastmoney.com/api/qt/stock/kline/get"
           f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
           f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f60,f61,f62"
           f"&klt=101&fqt=1&beg={beg}&end={end}&lmt=10000")

    for attempt in range(MAX_RETRY):
        cmd = ["curl", "-4", "-s", "--max-time", str(CURL_TIMEOUT),
               "-H", "User-Agent: Mozilla/5.0", url]
        try:
            raw = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8", errors="replace")
            if not raw.strip():
                time.sleep(0.5 * (attempt + 1))
                continue
            d = json.loads(raw)
            data = (d or {}).get("data") or {}
            klines = data.get("klines") or []
            if not klines:
                return code, name, []   # 空数据，不算失败

            records = []
            for kl in klines:
                parts = kl.split(",")
                if len(parts) < 8:
                    continue
                try:
                    records.append({
                        "code": code, "name": name,
                        "date": parts[0],
                        "open":    float(parts[1])  if parts[1]  else None,
                        "close":   float(parts[2])  if parts[2]  else None,
                        "high":    float(parts[3])  if parts[3]  else None,
                        "low":     float(parts[4])  if parts[4]  else None,
                        "volume":  float(parts[5])  if parts[5]  else None,
                        "amount":  float(parts[6])  if parts[6]  else None,
                        "amplitude":  float(parts[7])  if len(parts) > 7 and parts[7] else None,
                        "pct_change": float(parts[8])  if len(parts) > 8 and parts[8] else None,
                        "change":    float(parts[9])  if len(parts) > 9 and parts[9] else None,
                        "turnover":  float(parts[10]) if len(parts) > 10 and parts[10] else None,
                    })
                except (ValueError, IndexError):
                    continue
            return code, name, records
        except Exception:
            time.sleep(1 * (attempt + 1))

    return code, name, None   # None = 失败


def save_batch(conn, records):
    c = conn.cursor()
    c.executemany("""INSERT OR REPLACE INTO stocks
        (code,name,date,open,high,low,close,volume,amount,amplitude,pct_change,change,turnover)
        VALUES (:code,:name,:date,:open,:high,:low,:close,:volume,:amount,:amplitude,:pct_change,:change,:turnover)""", records)
    conn.commit()


def main():
    parser = argparse.ArgumentParser(description="A股全市场数据下载器（并发版）")
    parser.add_argument("--update",  action="store_true", help="增量更新")
    parser.add_argument("--codes",   type=str, help="指定股票代码，逗号分隔")
    parser.add_argument("--years",   type=int, default=3, help="下载年数（默认3年）")
    parser.add_argument("--from",    dest="from_date", type=str, help="起始日期 YYYYMMDD")
    parser.add_argument("--to",      dest="to_date",   type=str, help="结束日期 YYYYMMDD")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS, help=f"并发数（默认{MAX_WORKERS}）")
    args = parser.parse_args()

    init_db()
    conn = sqlite3.connect(DB_PATH)

    end_date   = args.to_date or datetime.now().strftime("%Y%m%d")
    start_date = args.from_date or (datetime.now() - timedelta(days=365 * args.years)).strftime("%Y%m%d")
    print(f"数据范围: {start_date} ~ {end_date}")
    print(f"并发线程数: {args.workers}\n")

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",")]
        stocks = [{"code": c, "name": c} for c in codes]
        print(f"[1/3] 指定股票: {len(codes)} 只")
    else:
        stocks = get_stock_list(conn)

    latest = None
    if args.update:
        c = conn.cursor()
        c.execute("SELECT MAX(date) FROM stocks")
        row = c.fetchone()
        latest = row[0] if row and row[0] else None
        if latest:
            start_date = (datetime.strptime(latest, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y%m%d")
            print(f"[增量更新] 从 {latest} 之后开始\n")

    total = len(stocks)
    print(f"[2/3] 开始并发下载（{args.workers}线程，共 {total} 只）...")

    success_cnt = 0
    skip_cnt    = 0
    error_cnt   = 0
    batch       = []
    start_time  = time.time()
    lock        = threading.Lock()
    progress    = {"done": 0}

    def handle_result(future):
        nonlocal success_cnt, skip_cnt, error_cnt, batch
        code, name, records = future.result()
        with lock:
            progress["done"] += 1
            done = progress["done"]
            elapsed = time.time() - start_time
            speed = done / elapsed if elapsed > 0 else 0
            eta = (total - done) / speed if speed > 0 else 0
            bar = f"[{done}/{total}] {code} {name} | ✅{success_cnt} ⏭{skip_cnt} ❌{error_cnt} | {elapsed:.0f}s | ~{eta:.0f}s剩余"
            # 清行 + 打印
            print("\r" + " " * 80 + "\r" + bar, end="", flush=True)

            if records is None:
                error_cnt += 1
            elif records:
                batch.extend(records)
                success_cnt += 1
            else:
                skip_cnt += 1

            # 批量写入
            if len(batch) >= BATCH_SAVE * 10:
                save_batch(conn, batch)
                batch = []

    # 提交任务
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = []
        for stock in stocks:
            future = executor.submit(download_one, stock, start_date, end_date)
            future.add_done_callback(handle_result)
            futures.append(future)

        # 等待全部完成
        for future in as_completed(futures):
            pass  # 结果在 callback 里处理

    # 最后一批写入
    if batch:
        save_batch(conn, batch)

    total_time = time.time() - start_time
    print(f"\n\n[3/3] 完成！成功:{success_cnt} ⏭跳过:{skip_cnt} ❌失败:{error_cnt} | 总耗时:{total_time:.0f}s ({total_time/60:.1f}分钟)")

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
