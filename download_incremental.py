#!/usr/bin/python3
"""
增量补下载：每次只下 N 只缺失的股票，避免触发 API 封锁
用法:
    python3 download_incremental.py          # 每次补 50 只
    python3 download_incremental.py --batch 100  # 每次补 100 只
    python3 download_incremental.py --delay 5    # 间隔 5 秒（更保守）
"""

import sqlite3
import time
import os
import sys
import subprocess
import json
from datetime import datetime, timedelta

DB_PATH = os.path.expanduser("~/stock-data/all_stocks.db")
DELAY = 3        # 请求间隔（秒），3秒比较安全
BATCH_SIZE = 50  # 每次补多少只


def get_missing_stocks(conn, batch_size):
    """找出还没有K线数据的股票"""
    c = conn.cursor()
    c.execute("""SELECT sl.code, sl.name 
                 FROM stock_list sl 
                 WHERE sl.code NOT IN (SELECT DISTINCT code FROM stocks)
                 ORDER BY sl.code
                 LIMIT ?""", (batch_size,))
    return [{"code": r[0], "name": r[1]} for r in c.fetchall()]


def get_all_missing_count(conn):
    """统计还有多少只没下载"""
    c = conn.cursor()
    c.execute("""SELECT COUNT(*) FROM stock_list 
                 WHERE code NOT IN (SELECT DISTINCT code FROM stocks)""")
    return c.fetchone()[0]


def code_to_secid(code):
    code = str(code).strip()
    return f"1.{code}" if code.startswith("6") else f"0.{code}"


def download_one(code, name, start_date, end_date):
    secid = code_to_secid(code)
    beg = start_date if len(start_date) == 8 else f"{start_date[:4]}{start_date[5:7]}{start_date[8:10]}"
    end = end_date   if len(end_date)   == 8 else f"{end_date[:4]}{end_date[5:7]}{end_date[8:10]}"

    url = (f"https://push2his.eastmoney.com/api/qt/stock/kline/get"
           f"?secid={secid}&fields1=f1,f2,f3,f4,f5,f6"
           f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f60,f61,f62"
           f"&klt=101&fqt=1&beg={beg}&end={end}&lmt=10000")

    for attempt in range(2):
        cmd = ["curl", "-4", "-s", "--max-time", "20",
               "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
               "-H", "Referer: https://quote.eastmoney.com/",
               url]
        try:
            raw = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode("utf-8", errors="replace")
            if not raw.strip():
                time.sleep(2)
                continue
            d = json.loads(raw)
            data = (d or {}).get("data") or {}
            klines = data.get("klines") or []
            if not klines:
                return []  # 空数据，跳过

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
            return records
        except Exception:
            time.sleep(3)
    return None  # 失败


def save_batch(conn, records):
    c = conn.cursor()
    c.executemany("""INSERT OR REPLACE INTO stocks
        (code,name,date,open,high,low,close,volume,amount,amplitude,pct_change,change,turnover)
        VALUES (:code,:name,:date,:open,:high,:low,:close,:volume,:amount,:amplitude,:pct_change,:change,:turnover)""", records)
    conn.commit()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch",  type=int, default=BATCH_SIZE, help=f"每次补多少只（默认{BATCH_SIZE}）")
    parser.add_argument("--delay",  type=float, default=DELAY, help=f"请求间隔秒数（默认{DELAY}）")
    parser.add_argument("--years", type=int, default=3, help="下载年数（默认3年）")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)

    end_date   = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=365 * args.years)).strftime("%Y%m%d")

    missing = get_all_missing_count(conn)
    stocks = get_missing_stocks(conn, args.batch)

    if not stocks:
        print("✅ 所有股票已下载完毕！")
        conn.close()
        return

    print(f"数据范围: {start_date} ~ {end_date}")
    print(f"还剩 {missing} 只未下载，本次补 {len(stocks)} 只，间隔 {args.delay}s/只\n")

    success = 0
    failed  = 0
    batch   = []
    start   = time.time()

    for i, stock in enumerate(stocks):
        code = stock["code"]
        name = stock.get("name", code)
        print(f"  [{i+1}/{len(stocks)}] {code} {name} ...", end=" ", flush=True)

        records = download_one(code, name, start_date, end_date)
        if records is None:
            print("❌ 失败（API无响应，可能是被限流）")
            failed += 1
            # 连续失败就停下来，避免被封更久
            if failed >= 3:
                print("\n⚠️ 连续失败3次，API可能被限流，暂停。请1小时后再试。")
                break
        elif records:
            print(f"✅ {len(records)}条")
            batch.extend(records)
            success += 1
            failed = 0  # 重置失败计数
        else:
            print("⏭ 无数据")
            failed = 0

        if len(batch) >= 200:
            save_batch(conn, batch)
            batch = []

        time.sleep(args.delay)

    if batch:
        save_batch(conn, batch)

    elapsed = time.time() - start
    remaining = get_all_missing_count(conn)
    print(f"\n完成！本次成功:{success} 失败:{failed}")
    print(f"还剩 {remaining} 只未下载")
    print(f"耗时 {elapsed:.0f}秒")

    if remaining > 0:
        print(f"\n💡 下次运行：")
        print(f"   /usr/bin/python3 ~/stock-tools/download_incremental.py")

    conn.close()


if __name__ == "__main__":
    main()
