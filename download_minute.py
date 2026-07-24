#!/usr/bin/env python3
"""
分钟数据下载器（Tushare Pro · pro_bar接口）

支持：股票(E) / 指数(I) / 基金(FD) / 期货(FT)
频度：1min / 5min / 15min / 30min / 60min

关键限制：
  - 分钟数据需要单独开权限（联系微信 waditu_a 或QQ群）
  - 单次最多8000行，超出自动分段
  - 时间参数必须带时分秒：2020-01-07 09:00:00
  - 每天收盘后17~21点更新

用法：
  # 单只股票1分钟
  /usr/bin/python3 ~/stock-tools/download_minute.py \
      -c 600000.SH -f 1min -s "2020-01-07 09:00:00" -e "2020-01-08 17:00:00"

  # 指数5分钟
  /usr/bin/python3 ~/stock-tools/download_minute.py \
      -c 000001.SH -a I -f 5min -s "2024-01-01 09:00:00"

  # 批量下载（从文件读取标的列表，每行一个代码）
  /usr/bin/python3 ~/stock-tools/download_minute.py \
      --codes-file ~/stock-data/watchlist.txt -f 30min

  # 多标的（逗号分隔）
  /usr/bin/python3 ~/stock-tools/download_minute.py \
      -c "600519.SH,000858.SZ,000568.SZ" -f 15min -s "2024-07-01 09:00:00"

前置依赖：
  pip3 install --user tushare

Token配置：
  export TUSHARE_TOKEN=你的token
"""

import os, sys, sqlite3, time, argparse, math
from datetime import datetime, timedelta


# ============ 配置 ============

TOKEN = os.environ.get("TUSHARE_TOKEN", "")
TOKEN_FILE = os.path.expanduser("~/.tushare_token")
if not TOKEN and os.path.exists(TOKEN_FILE):
    with open(TOKEN_FILE) as f:
        TOKEN = f.read().strip()

DEFAULT_DB = os.path.expanduser("~/stock-data/minute.db")
MAX_ROWS = 8000           # 单次API返回上限
RATE_LIMIT_CALLS = 190
RATE_LIMIT_WINDOW = 60
CALL_GAP = 0.1

# 资产类型映射
ASSET_MAP = {
    "E":  "股票",
    "I":  "指数",
    "FD": "基金",
    "FT": "期货",
}

# 分钟表结构
MINUTE_DDL = """
CREATE TABLE IF NOT EXISTS minute_data (
    ts_code    TEXT NOT NULL,
    trade_time TEXT NOT NULL,
    freq       TEXT NOT NULL,
    open       REAL,
    high       REAL,
    low        REAL,
    close      REAL,
    vol        REAL,
    amount     REAL,
    PRIMARY KEY (ts_code, trade_time, freq)
);
CREATE INDEX IF NOT EXISTS idx_min_ts_code ON minute_data(ts_code);
CREATE INDEX IF NOT EXISTS idx_min_time    ON minute_data(trade_time);
CREATE TABLE IF NOT EXISTS download_log (
    ts_code    TEXT NOT NULL,
    freq       TEXT NOT NULL,
    start_time TEXT,
    end_time   TEXT,
    rows       INTEGER,
    status     TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
"""


def init_db(db_path):
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(MINUTE_DDL)
    conn.commit()
    return conn


def time_add(t_str, **kwargs):
    """时间字符串加减"""
    dt = datetime.strptime(t_str, "%Y-%m-%d %H:%M:%S")
    return (dt + timedelta(**kwargs)).strftime("%Y-%m-%d %H:%M:%S")


def split_time_range(start, end, max_days=5):
    """把长时间范围切成小段，控制每次返回在8000行以内"""
    segments = []
    current = start
    while current < end:
        seg_end = time_add(current, days=max_days)
        if seg_end > end:
            seg_end = end
        segments.append((current, seg_end))
        current = seg_end
    return segments


def download_segment(pro, ts_code, freq, asset, start, end):
    """下载一个时间段的分钟数据，带重试"""
    for attempt in range(3):
        try:
            df = pro.pro_bar(
                ts_code=ts_code,
                asset=asset,
                freq=freq,
                start_date=start,
                end_date=end
            )
            return df
        except Exception as e:
            if attempt < 2:
                time.sleep(1)
            else:
                print(f"\n  ⚠ [{ts_code}] {start}~{end} 失败: {e}")
                return None
    return None


def save_minute(conn, df, freq):
    """写入分钟数据"""
    cur = conn.cursor()
    n = 0
    for _, row in df.iterrows():
        try:
            cur.execute(
                """INSERT OR REPLACE INTO minute_data
                   (ts_code, trade_time, freq, open, high, low, close, vol, amount)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(row['ts_code']),
                    str(row['trade_time']),
                    freq,
                    float(row.get('open', 0) or 0),
                    float(row.get('high', 0) or 0),
                    float(row.get('low', 0) or 0),
                    float(row.get('close', 0) or 0),
                    float(row.get('vol', 0) or 0),
                    float(row.get('amount', 0) or 0),
                )
            )
            n += 1
        except (ValueError, TypeError, KeyError):
            pass
    conn.commit()
    return n


def log_download(conn, ts_code, freq, start, end, rows, status):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO download_log (ts_code, freq, start_time, end_time, rows, status) VALUES (?,?,?,?,?,?)",
        (ts_code, freq, start, end, rows, status)
    )
    conn.commit()


def download_one_stock(pro, conn, ts_code, freq, asset, start, end):
    """下载单个标的的分钟数据，自动分段"""
    segments = split_time_range(start, end, max_days=5)
    total_rows = 0
    errors = 0

    for seg_idx, (seg_start, seg_end) in enumerate(segments):
        df = download_segment(pro, ts_code, freq, asset, seg_start, seg_end)

        if df is not None and not df.empty:
            n = save_minute(conn, df, freq)
            total_rows += n
        else:
            errors += 1

        # 进度
        pct = (seg_idx + 1) / len(segments) * 100
        bar = (f"\r    [{ts_code}] 段{seg_idx+1}/{len(segments)} {pct:.0f}%"
               f" | {total_rows:,}行")
        print(bar + " " * 5, end="", flush=True)

        if seg_idx < len(segments) - 1:
            time.sleep(CALL_GAP)

    status = "ok" if errors == 0 else f"partial({errors}/{len(segments)}失败)"
    log_download(conn, ts_code, freq, start, end, total_rows, status)
    return total_rows, errors


def main():
    parser = argparse.ArgumentParser(
        description="Tushare Pro · 分钟数据下载器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
资产类型:
  E     股票 (默认)
  I     指数
  FD    基金
  FT    期货

频度: 1min / 5min / 15min / 30min / 60min

示例:
  %(prog)s -c 600000.SH -f 1min -s "2020-01-07 09:00:00" -e "2020-01-08 17:00:00"
  %(prog)s -c 000001.SH -a I -f 5min -s "2024-01-01 09:00:00"
  %(prog)s -c "600519.SH,000858.SZ" -f 30min
  %(prog)s --codes-file watchlist.txt -f 15min -s "2024-07-01 09:00:00"
        """
    )
    parser.add_argument("--codes", "-c", type=str, help="股票代码，多个用逗号分隔")
    parser.add_argument("--codes-file", type=str, help="从文件读取股票代码列表，每行一个")
    parser.add_argument("--asset", "-a", type=str, default="E",
                        help=f"资产类型: E=股票(默认)/I=指数/FD=基金/FT=期货")
    parser.add_argument("--freq", "-f", type=str, default="1min",
                        help="频度: 1min/5min/15min/30min/60min (默认1min)")
    parser.add_argument("--start", "-s", type=str, help="开始时间 (如 2024-01-01 09:00:00)")
    parser.add_argument("--end", "-e", type=str, help="结束时间")
    parser.add_argument("--db", type=str, default=DEFAULT_DB,
                        help=f"数据库路径 (默认: {DEFAULT_DB})")
    parser.add_argument("--token", "-t", type=str, help="Tushare Token")
    parser.add_argument("--last", type=str, help="下载最近N天 (如 --last 7)")
    args = parser.parse_args()

    # Token
    token = args.token or TOKEN
    if not token:
        print("❌ 未配置Token"); sys.exit(1)

    import tushare as ts
    ts.set_token(token)
    pro = ts.pro_api()

    # 读取标的列表
    codes = []
    if args.codes_file:
        with open(os.path.expanduser(args.codes_file)) as f:
            codes = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    elif args.codes:
        codes = [c.strip() for c in args.codes.split(",")]
    else:
        print("❌ 请指定股票代码: -c 或 --codes-file")
        sys.exit(1)

    # 时间范围
    now = datetime.now()
    if args.last:
        days = int(args.last)
        start = (now - timedelta(days=days)).strftime("%Y-%m-%d 09:00:00")
        end = now.strftime("%Y-%m-%d 17:00:00")
    else:
        start = args.start or now.strftime("%Y-%m-%d 09:00:00")
        end = args.end or now.strftime("%Y-%m-%d 17:00:00")

    freq = args.freq
    asset = args.asset
    asset_name = ASSET_MAP.get(asset, asset)

    # 初始化和打印
    conn = init_db(args.db)

    print("=" * 60)
    print("  Tushare Pro · 分钟数据下载器")
    print("=" * 60)
    print(f"  📋 标的:   {len(codes)}个 {asset_name}")
    print(f"  ⏱  频度:   {freq}")
    print(f"  📅 范围:   {start} ~ {end}")
    print(f"  💾 数据库: {args.db}")
    print(f"  ⚠  限制:   单次≤{MAX_ROWS}行，自动分段")
    print()

    # 逐标的下载
    overall_rows = 0
    overall_errors = 0
    t0 = time.time()

    for idx, code in enumerate(codes):
        call_count = 0

        print(f"[{idx+1}/{len(codes)}] {code} {asset_name} {freq}")
        rows, errs = download_one_stock(pro, conn, code, freq, asset, start, end)
        overall_rows += rows
        overall_errors += errs

        if rows > 0:
            print(f"  ✅ {rows:,}行")
        else:
            print(f"  ⚠ 无数据（检查权限和时间范围）")
            # 检查权限提示
            if rows == 0 and errs == len(split_time_range(start, end)):
                print(f"     💡 分钟数据需要单独开权限，联系微信 waditu_a")

    total_t = time.time() - t0

    print(f"\n{'='*60}")
    print(f"  ✅ 完成！{overall_rows:,}行 | 耗时 {total_t:.0f}s")
    print(f"{'='*60}")

    # 数据库统计
    cur = conn.cursor()
    cur.execute("SELECT COUNT(DISTINCT ts_code) FROM minute_data WHERE freq=?", (freq,))
    stocks = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM minute_data WHERE freq=?", (freq,))
    total = cur.fetchone()[0]
    print(f"\n📈 数据库 ({freq}):")
    print(f"  标的数: {stocks}")
    print(f"  总行数: {total:,}")
    print()

    conn.close()


if __name__ == "__main__":
    main()
