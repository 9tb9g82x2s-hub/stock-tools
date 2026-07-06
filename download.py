#!/usr/bin/python3
"""
A股全市场历史数据下载器（curl版）
使用 curl -4 调用东方财富 API，绕过 Python urllib3/LibreSSL 兼容问题

用法:
    python3 download.py              # 下载全量数据（耗时20-40分钟）
    python3 download.py --update     # 仅更新时间到最新交易日
    python3 download.py --codes 600519,600105   # 只下载指定股票
"""

import sqlite3
import time
import os
import sys
import argparse
import subprocess
import json
from datetime import datetime, timedelta

DB_PATH = os.path.expanduser("~/stock-data/all_stocks.db")
DELAY = 0.2   # API 调用间隔（秒）
BATCH_SAVE = 50
CURL_TIMEOUT = 20


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
    """获取A股股票列表
    优先从本地数据库读取，失败再用 curl/akshare 联网获取
    """
    # 先从本地数据库读
    c = conn.cursor()
    c.execute("SELECT code, name FROM stock_list ORDER BY code")
    rows = c.fetchall()
    if rows and len(rows) > 100:
        print(f"[1/3] 从本地数据库读取股票列表: {len(rows)} 只")
        return [{"code": r[0], "name": r[1]} for r in rows]

    # 本地没有，联网获取
    print("[1/3] 本地无列表，联网获取A股股票列表...")
    all_stocks = []
    page = 1
    page_size = 100    # 东方财富接口每页最多返回100条
    max_pages = 100      # 最多100页 = 10000只
    max_retry = 3

    while page <= max_pages:
        url = (f"https://push2.eastmoney.com/api/qt/clist/get"
                f"?pn={page}&pz={page_size}&po=1&np=1&fltt=2&invt=2&fid=f3"
                f"&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
                f"&fields=f12,f14")
        cmd = ["curl", "-4", "-s", "--max-time", "15", "--retry", "2",
               "-H", "User-Agent: Mozilla/5.0",
               "-H", "Referer: https://quote.eastmoney.com",
               url]
        items = None
        for attempt in range(max_retry):
            try:
                raw = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8", errors="replace")
                d = json.loads(raw)
                items = (d.get("data") or {}).get("diff") or []
                break
            except Exception as e:
                print(f"  第{page}页第{attempt+1}次失败: {e}")
                time.sleep(0.5)
                items = None

        if items is None:
            print(f"  [警告] 第{page}页重试{max_retry}次均失败，跳过")
            page += 1
            continue

        if not items:
            break

        for item in items:
            code = str(item.get("f12") or "").strip()
            name = str(item.get("f14") or "").strip()
            if code and name and "退" not in name:
                all_stocks.append({"code": code, "name": name})

        print(f"  第{page}页: +{len(items)}条，累计 {len(all_stocks)} 只", flush=True)
        if len(items) < page_size:
            break
        page += 1
        time.sleep(0.15)

    if all_stocks:
        print(f"  共获取 {len(all_stocks)} 只股票")
        # 保存到数据库
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for s in all_stocks:
            c.execute("INSERT OR REPLACE INTO stock_list (code, name, updated_at) VALUES (?, ?, ?)",
                      (s["code"], s["name"], now))
        conn.commit()
        return all_stocks

    # 最终备选：akshare
    print("  [备选] 尝试 akshare...")
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        stocks = []
        for _, row in df.iterrows():
            code = str(row["code"]).strip()
            name = str(row["name"]).strip()
            if code and name and "退" not in name:
                stocks.append({"code": code, "name": name})
        print(f"  [备选] 获取到 {len(stocks)} 只股票")
        return stocks
    except Exception as e2:
        print(f"  [错误] 备选也失败: {e2}")

    print("[错误] 无法获取股票列表，请检查网络")
    sys.exit(1)

    # 备选：用 akshare
    print("  [备选] 尝试 akshare...")
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        stocks = []
        for _, row in df.iterrows():
            code = str(row["code"]).strip()
            name = str(row["name"]).strip()
            if code and name and "退" not in name:
                stocks.append({"code": code, "name": name})
        print(f"  [备选] 获取到 {len(stocks)} 只股票")
        return stocks
    except Exception as e2:
        print(f"  [错误] 备选也失败: {e2}")

    print("[错误] 无法获取股票列表，请检查网络")
    sys.exit(1)


def get_latest_date(conn):
    c = conn.cursor()
    c.execute("SELECT MAX(date) FROM stocks")
    row = c.fetchone()
    return row[0] if row and row[0] else None


def code_to_secid(code):
    """股票代码 → EastMoney secid（0=深市 1=沪市）"""
    code = str(code).strip()
    if code.startswith("6"):
        return f"1.{code}"   # 上海
    else:
        return f"0.{code}"   # 深圳


def download_stock_data(code, name, start_date, end_date):
    """用 curl 下载单只股票历史K线，带重试"""
    secid = code_to_secid(code)
    beg = start_date if len(start_date) == 8 else f"{start_date[:4]}{start_date[5:7]}{start_date[8:10]}"
    end = end_date   if len(end_date)   == 8 else f"{end_date[:4]}{end_date[5:7]}{end_date[8:10]}"

    url = (f"https://push2his.eastmoney.com/api/qt/stock/kline/get"
            f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
            f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f60,f61,f62"
            f"&klt=101&fqt=1&beg={beg}&end={end}&lmt=10000")

    for attempt in range(3):
        cmd = ["curl", "-4", "-s", "--max-time", str(CURL_TIMEOUT),
               "-H", "User-Agent: Mozilla/5.0", url]
        try:
            raw = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8", errors="replace")
            if not raw.strip():
                time.sleep(1 * (attempt + 1))
                continue
            d = json.loads(raw)
            data = (d or {}).get("data") or {}
            klines = data.get("klines") or []
            if not klines:
                return [], name

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
            return records, name
        except (subprocess.CalledProcessError, json.JSONDecodeError, Exception):
            time.sleep(1.5 * (attempt + 1))
    return None, name


def save_batch(conn, records):
    c = conn.cursor()
    c.executemany("""INSERT OR REPLACE INTO stocks
        (code,name,date,open,high,low,close,volume,amount,amplitude,pct_change,change,turnover)
        VALUES (:code,:name,:date,:open,:high,:low,:close,:volume,:amount,:amplitude,:pct_change,:change,:turnover)""", records)
    conn.commit()


def save_stock_list(conn, stocks):
    c = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for s in stocks:
        c.execute("INSERT OR REPLACE INTO stock_list (code,name,updated_at) VALUES (?,?,?)",
                  (s["code"], s["name"], now))
    conn.commit()


def main():
    parser = argparse.ArgumentParser(description="A股全市场数据下载器（curl版）")
    parser.add_argument("--update",  action="store_true", help="增量更新")
    parser.add_argument("--codes",   type=str, help="指定股票代码，逗号分隔")
    parser.add_argument("--years",   type=int, default=3, help="下载年数（默认3年）")
    parser.add_argument("--from",    dest="from_date", type=str, help="起始日期 YYYYMMDD")
    parser.add_argument("--to",      dest="to_date",   type=str, help="结束日期 YYYYMMDD")
    parser.add_argument("--delay",   type=float, default=DELAY, help="API间隔（秒）")
    args = parser.parse_args()

    init_db()
    conn = sqlite3.connect(DB_PATH)

    end_date   = args.to_date or datetime.now().strftime("%Y%m%d")
    start_date = args.from_date or (datetime.now() - timedelta(days=365 * args.years)).strftime("%Y%m%d")
    print(f"数据范围: {start_date} ~ {end_date}\n")

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",")]
        print(f"[1/3] 指定股票: {len(codes)} 只")
        stocks = [{"code": c, "name": c} for c in codes]
    else:
        stocks = get_stock_list(conn)

    latest = get_latest_date(conn)
    if args.update and latest:
        start_date = (datetime.strptime(latest, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y%m%d")
        print(f"[增量更新] 从 {latest} 之后开始\n")

    if not args.codes:
        save_stock_list(conn, stocks)

    print(f"[2/3] 开始下载（间隔 {args.delay}s/股，共 {len(stocks)} 只）...")

    total        = len(stocks)
    batch        = []
    success_cnt  = 0
    skip_cnt     = 0
    start_time   = time.time()

    for i, stock in enumerate(stocks):
        code = stock["code"]
        name = stock.get("name", code)
        elapsed = time.time() - start_time
        done    = i + 1
        eta     = (elapsed / done * (total - done)) if done > 0 else 0
        print(f"\r  [{done}/{total}] {code} {name} | 成功:{success_cnt} 跳过:{skip_cnt} | {elapsed:.0f}s elapsed, ~{eta:.0f}s left",
              end="", flush=True)

        records, _ = download_stock_data(code, name, start_date, end_date)
        if records is None:
            skip_cnt += 1
        elif records:
            batch.extend(records)
            success_cnt += 1
        else:
            skip_cnt += 1

        if len(batch) >= BATCH_SAVE * 200:
            save_batch(conn, batch)
            batch = []
            print(f"\r  [{done}/{total}] ...已保存 {success_cnt} 只", end="", flush=True)

        time.sleep(args.delay)

    if batch:
        save_batch(conn, batch)

    print(f"\n[3/3] 完成！成功:{success_cnt} | 跳过:{skip_cnt} | 总耗时:{time.time()-start_time:.0f}s")

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
