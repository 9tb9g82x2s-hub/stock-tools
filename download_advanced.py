#!/usr/bin/env python3
"""
进阶数据下载器：筹码分布 + 板块数据（gycloud HTTP通道）

用于板块轮动回测和多因子分析

用法：
  /usr/bin/python3 ~/stock-tools/download_advanced.py              # 下载今天
  /usr/bin/python3 ~/stock-tools/download_advanced.py --update     # 增量更新
  /usr/bin/python3 ~/stock-tools/download_advanced.py -s 20240101 -e 20241231
"""

import os, sys, sqlite3, time, argparse, requests
from datetime import datetime, timedelta

TOKEN = "2b6b1b830a45468b9856e6500ce40a90"
BASE_URL = "https://ts.gyzcloud.top/api"
DB = os.path.expanduser("~/stock-data/advanced.db")
MAX_RETRIES = 3
CALL_GAP = 0.5  # 每秒不超过2次，避免频限

# ============ 建表DDL ============

DDL = """
-- 筹码分布
CREATE TABLE IF NOT EXISTS cyq_perf (
    ts_code     TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    his_low     REAL,  his_high    REAL,
    cost_5pct   REAL,  cost_15pct  REAL,
    cost_50pct  REAL,  cost_85pct  REAL,
    cost_95pct  REAL,  weight_avg  REAL,
    winner_rate REAL,
    PRIMARY KEY (ts_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_cyq_date ON cyq_perf(trade_date);

-- 申万行业日线
CREATE TABLE IF NOT EXISTS sw_daily (
    ts_code     TEXT NOT NULL,
    trade_date  TEXT NOT NULL,
    open        REAL, high    REAL, low     REAL, close   REAL,
    vol         REAL, amount  REAL,
    pct_change  REAL,
    PRIMARY KEY (ts_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_sw_date ON sw_daily(trade_date);

-- 同花顺概念板块列表
CREATE TABLE IF NOT EXISTS ths_index (
    ts_code     TEXT PRIMARY KEY,
    name        TEXT,
    type        TEXT,
    list_date   TEXT
);

-- 概念板块成分股
CREATE TABLE IF NOT EXISTS ths_member (
    ts_code     TEXT NOT NULL,
    con_code    TEXT NOT NULL,
    name        TEXT,
    in_date     TEXT,
    out_date    TEXT,
    is_new      TEXT,
    PRIMARY KEY (ts_code, con_code)
);
CREATE INDEX IF NOT EXISTS idx_ths_code ON ths_member(ts_code);
"""


def init_db():
    os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.executescript(DDL)
    conn.commit()
    return conn


def call_api(api_name, params, fields=""):
    """gycloud HTTP调用，带重试"""
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.post(
                f"{BASE_URL}/{api_name}",
                json={"api_name": api_name, "token": TOKEN, "params": params, "fields": fields},
                timeout=15
            )
            if r.status_code == 200:
                d = r.json()
                if d.get("code") == 0:
                    return d.get("data", {}).get("items", [])
                elif d.get("code") == -2001:
                    time.sleep(3)
                    continue
            if attempt < MAX_RETRIES - 1:
                time.sleep(1)
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(1)
    return None


# ============ 筹码分布 ============

def download_cyq_perf(conn, trade_date):
    """下载某日全市场筹码分布（单次调用）"""
    items = call_api("cyq_perf", {"trade_date": trade_date},
                     "ts_code,trade_date,his_low,his_high,cost_5pct,cost_15pct,cost_50pct,cost_85pct,cost_95pct,weight_avg,winner_rate")
    if items is None:
        return None
    cur = conn.cursor()
    n = 0
    for row in items:
        try:
            cur.execute("""INSERT OR REPLACE INTO cyq_perf VALUES (
                ?,?,?,?,?,?,?,?,?,?,?)""", (
                str(row[0]), str(row[1]),
                float(row[2] or 0), float(row[3] or 0),
                float(row[4] or 0), float(row[5] or 0),
                float(row[6] or 0), float(row[7] or 0),
                float(row[8] or 0), float(row[9] or 0),
                float(row[10] or 0),
            ))
            n += 1
        except (ValueError, TypeError, IndexError):
            pass
    conn.commit()
    return n


# ============ 申万行业 ============

def download_sw_daily(conn, trade_date):
    """下载某日申万行业日线"""
    items = call_api("sw_daily", {"trade_date": trade_date},
                     "ts_code,trade_date,open,high,low,close,vol,amount,pct_change")
    if items is None:
        return None
    cur = conn.cursor()
    n = 0
    for row in items:
        try:
            cur.execute("""INSERT OR REPLACE INTO sw_daily VALUES (
                ?,?,?,?,?,?,?,?,?)""", (
                str(row[0]), str(row[1]),
                float(row[2] or 0), float(row[3] or 0),
                float(row[4] or 0), float(row[5] or 0),
                float(row[6] or 0), float(row[7] or 0),
                float(row[8] or 0),
            ))
            n += 1
        except (ValueError, TypeError, IndexError):
            pass
    conn.commit()
    return n


# ============ 概念板块 ============

def download_ths_index(conn):
    """下载同花顺概念板块列表（一次性）"""
    items = call_api("ths_index", {"exchange": "A", "type": "N"},
                     "ts_code,name,type,list_date")
    if items is None:
        return None
    cur = conn.cursor()
    n = 0
    for row in items:
        try:
            cur.execute("INSERT OR REPLACE INTO ths_index VALUES (?,?,?,?)",
                        (str(row[0]), str(row[1]), str(row[2]) if len(row) > 2 else "",
                         str(row[3]) if len(row) > 3 else ""))
            n += 1
        except:
            pass
    conn.commit()
    return n


def download_ths_member(conn, ts_code):
    """下载某个概念板块成分股"""
    items = call_api("ths_member", {"ts_code": ts_code},
                     "ts_code,con_code,name,in_date,out_date,is_new")
    if items is None:
        return None
    cur = conn.cursor()
    n = 0
    for row in items:
        try:
            cur.execute("INSERT OR REPLACE INTO ths_member VALUES (?,?,?,?,?,?)",
                        (str(row[0]), str(row[1]), str(row[2]),
                         str(row[3]) if len(row) > 3 else "",
                         str(row[4]) if len(row) > 4 else "",
                         str(row[5]) if len(row) > 5 else ""))
            n += 1
        except:
            pass
    conn.commit()
    return n


# ============ 辅助 ============

def get_trade_dates(start, end):
    items = call_api("trade_cal", {"exchange": "SSE", "start_date": start, "end_date": end, "is_open": 1}, "cal_date")
    if not items:
        return []
    return sorted([d[0] for d in items])


def format_duration(s):
    if s < 60: return f"{s:.0f}s"
    elif s < 3600: return f"{s/60:.1f}分钟"
    else: return f"{s//3600:.0f}h{(s%3600)//60:.0f}m"


# ============ 主流程 ============

def main():
    parser = argparse.ArgumentParser(description="进阶数据下载器：筹码分布 + 板块数据")
    parser.add_argument("--start", "-s", type=str, help="开始日期 YYYYMMDD")
    parser.add_argument("--end", "-e", type=str, help="结束日期 YYYYMMDD")
    parser.add_argument("--update", "-u", action="store_true", help="增量更新")
    parser.add_argument("--today", action="store_true", help="仅下载今天")
    parser.add_argument("--module", "-m", type=str, default="all",
                        help="下载模块: all, cyq, sw, ths (默认all)")
    args = parser.parse_args()

    conn = init_db()
    today = datetime.now().strftime("%Y%m%d")

    # 日期范围
    if args.today:
        start = end = today
    elif args.update:
        cur = conn.cursor()
        cur.execute("SELECT MAX(trade_date) FROM sw_daily")
        r = cur.fetchone()
        last = r[0] if r and r[0] else None
        start = (datetime.strptime(last, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d") if last else "20240101"
        end = today
    else:
        start = args.start or today
        end = args.end or today

    module = args.module
    do_cyq = module in ("all", "cyq")
    do_sw = module in ("all", "sw")
    do_ths = module in ("all", "ths")

    print("=" * 60)
    print("  进阶数据下载器：筹码分布 + 板块数据")
    print("=" * 60)
    print(f"  模块: {module}")
    print(f"  日期: {start} ~ {end}")
    print()

    # ---- 概念板块（一次性） ----
    if do_ths:
        print("📊 概念板块列表...", end=" ", flush=True)
        n = download_ths_index(conn)
        print(f"{n} 个板块")
        time.sleep(CALL_GAP)

        # 获取所有板块代码
        cur = conn.cursor()
        cur.execute("SELECT ts_code FROM ths_index")
        codes = [r[0] for r in cur.fetchall()]
        print(f"📊 板块成分股 ({len(codes)}个)...", end="", flush=True)
        time.sleep(CALL_GAP)
        n_mem = 0
        for i, code in enumerate(codes):
            m = download_ths_member(conn, code)
            if m: n_mem += m
            if (i + 1) % 10 == 0:
                print(f"\r📊 板块成分股 ({len(codes)}个)... {i+1}/{len(codes)}", end="", flush=True)
            time.sleep(CALL_GAP * 0.5)
        print(f"\r📊 板块成分股: {n_mem} 条                                   ")

    # ---- 逐日下载 ----
    if do_cyq or do_sw:
        dates = get_trade_dates(start, end)
        total = len(dates)
        if total == 0:
            print("无交易日"); conn.close(); return

        print(f"📅 交易日: {total} 天, 预计~{format_duration(total * 1.5)}")
        print()

        cyq_total = 0; sw_total = 0
        t0 = time.time()

        for i, date in enumerate(dates):
            cyq_n = 0; sw_n = 0

            if do_cyq:
                cyq_n = download_cyq_perf(conn, date) or 0
                cyq_total += cyq_n
                time.sleep(CALL_GAP)

            if do_sw:
                sw_n = download_sw_daily(conn, date) or 0
                sw_total += sw_n
                time.sleep(CALL_GAP)

            elapsed = time.time() - t0
            pct = (i + 1) / total * 100
            eta = format_duration(elapsed / (i + 1) * (total - i - 1)) if i > 0 else "..."

            print(f"\r  [{i+1:>4}/{total}] {pct:5.1f}% | {date} | "
                  f"筹码+{cyq_n} | 行业+{sw_n} | 剩余~{eta}         ",
                  end="", flush=True)

        total_t = time.time() - t0
        print("\n")
        print("=" * 60)
        print(f"  ✅ 完成！筹码{cyq_total:,}条 + 行业{sw_total:,}条")
        print(f"  ⏱  耗时: {format_duration(total_t)}")
        print("=" * 60)

    # 统计
    cur = conn.cursor()
    print()
    for tbl, label in [("cyq_perf", "筹码分布"), ("sw_daily", "申万行业"), ("ths_index", "概念板块"), ("ths_member", "板块成分股")]:
        cur.execute(f"SELECT COUNT(*) FROM {tbl}")
        cnt = cur.fetchone()[0]
        if cnt > 0:
            cur.execute(f"SELECT MIN(trade_date), MAX(trade_date) FROM {tbl}" if "trade_date" in
                        [d[1] for d in cur.execute(f"PRAGMA table_info({tbl})").fetchall()] else "SELECT 1,1")
            try:
                mn, mx = cur.fetchone()
                print(f"  {label}: {cnt:,}条 | {mn}~{mx}")
            except:
                print(f"  {label}: {cnt:,}条")
    print()

    conn.close()


if __name__ == "__main__":
    main()
