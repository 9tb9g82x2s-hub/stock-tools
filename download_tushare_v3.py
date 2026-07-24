#!/usr/bin/env python3
"""
A股全市场日线数据下载器（Tushare Pro · trade_date循环模式）

核心理念：按交易日逐天拉全市场，不按股票代码循环
  5000+只股票 × N年 ≈ 蜗牛爬
  ~220个交易日/年      ≈ 优雅高效

用法：
  /usr/bin/python3 ~/stock-tools/download_tushare_v3.py                          # 下载今天
  /usr/bin/python3 ~/stock-tools/download_tushare_v3.py -s 20240101              # 从某天开始
  /usr/bin/python3 ~/stock-tools/download_tushare_v3.py -s 20240101 -e 20241231  # 日期范围
  /usr/bin/python3 ~/stock-tools/download_tushare_v3.py --update                 # 增量更新
  /usr/bin/python3 ~/stock-tools/download_tushare_v3.py --today                  # 仅今天

前置条件：
  pip3 install tushare
  注册 https://tushare.pro → 个人主页获取Token
  注册100积分 + 修改个人信息20积分 = 120积分即可高频访问

Token配置（三选一）：
  1. export TUSHARE_TOKEN=你的token
  2. echo '你的token' > ~/.tushare_token
  3. --token 参数
"""

import os, sys, sqlite3, time, argparse
from datetime import datetime, timedelta

# ============ 配置 ============

TOKEN = os.environ.get("TUSHARE_TOKEN", "")
TOKEN_FILE = os.path.expanduser("~/.tushare_token")
if not TOKEN and os.path.exists(TOKEN_FILE):
    with open(TOKEN_FILE) as f:
        TOKEN = f.read().strip()

DB_PATH = os.path.expanduser("~/stock-data/tushare_daily.db")
MAX_RETRIES = 3          # 单日失败重试次数
RETRY_DELAY = 1           # 重试间隔（秒）
RATE_LIMIT_CALLS = 190    # 每周期最多调用次数（留10次余量）
RATE_LIMIT_WINDOW = 60    # 速率窗口（秒），120积分=200次/分钟
COMMIT_EVERY = 20         # 每N天commit一次
CALL_GAP = 0.05           # 每次调用间隔（秒），降低被限流概率


# ============ 数据库 ============

def init_db(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS daily (
            ts_code    TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open       REAL, high REAL, low REAL, close REAL,
            pre_close  REAL, change REAL, pct_chg REAL,
            vol        REAL, amount REAL,
            PRIMARY KEY (ts_code, trade_date)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_daily_date ON daily(trade_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_daily_code ON daily(ts_code)")
    conn.commit()
    return conn


# ============ 核心逻辑 ============

def get_trade_dates(pro, start_date, end_date):
    """获取交易日历 — 只拿交易日，不拿休市日"""
    df = pro.trade_cal(
        exchange='SSE',
        start_date=start_date,
        end_date=end_date,
        is_open='1'
    )
    return sorted(df['cal_date'].tolist())


def download_one_date(pro, trade_date):
    """
    下载单个交易日全市场日线，带重试机制。
    失败自动重试 MAX_RETRIES 次，每次间隔 RETRY_DELAY 秒。
    """
    for attempt in range(MAX_RETRIES):
        try:
            df = pro.daily(trade_date=trade_date)
            return df
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
            else:
                print(f"\n  ⚠ {trade_date} 下载失败(重试{MAX_RETRIES}次): {e}")
                return None
    return None


def save_batch(cur, df):
    """将一天的数据批量写入数据库"""
    n = 0
    for _, row in df.iterrows():
        try:
            cur.execute(
                """INSERT OR REPLACE INTO daily
                   (ts_code, trade_date, open, high, low, close,
                    pre_close, change, pct_chg, vol, amount)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(row['ts_code']),
                    str(row['trade_date']),
                    float(row.get('open', 0) or 0),
                    float(row.get('high', 0) or 0),
                    float(row.get('low', 0) or 0),
                    float(row.get('close', 0) or 0),
                    float(row.get('pre_close', 0) or 0),
                    float(row.get('change', 0) or 0),
                    float(row.get('pct_chg', 0) or 0),
                    float(row.get('vol', 0) or 0),
                    float(row.get('amount', 0) or 0),
                )
            )
            n += 1
        except (ValueError, TypeError, KeyError):
            pass
    return n


def format_duration(seconds):
    """格式化耗时"""
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        return f"{seconds/60:.1f}分钟"
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}小时{m}分钟"


# ============ 主流程 ============

def main():
    parser = argparse.ArgumentParser(
        description="Tushare Pro · trade_date循环全市场下载器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s                                  # 下载今天
  %(prog)s -s 20240101                      # 从2024-01-01到今天
  %(prog)s -s 20240101 -e 20241231          # 指定范围
  %(prog)s --update                         # 增量更新（从库中最新日期接着下）
  %(prog)s --today                          # 仅今天
        """
    )
    parser.add_argument("--start", "-s", type=str, help="开始日期 YYYYMMDD")
    parser.add_argument("--end", "-e", type=str, help="结束日期 YYYYMMDD")
    parser.add_argument("--update", "-u", action="store_true", help="增量更新模式")
    parser.add_argument("--today", action="store_true", help="仅下载今天")
    parser.add_argument("--token", "-t", type=str, help="Tushare Token")
    parser.add_argument("--db", type=str, default=DB_PATH, help=f"数据库路径 (默认: {DB_PATH})")
    args = parser.parse_args()

    # ----- Token -----
    token = args.token or TOKEN
    if not token:
        print("❌ 未配置Tushare Token，请通过以下任一方式提供：")
        print("   1. export TUSHARE_TOKEN=你的token")
        print("   2. echo '你的token' > ~/.tushare_token")
        print("   3. --token 参数")
        print("\n📌 没有Token？去 https://tushare.pro 注册，个人主页拿Token")
        print("   注册100积分 + 修改个人信息20积分 = 120积分，足够用了")
        sys.exit(1)

    # ----- 初始化 Tushare Pro -----
    import tushare as ts
    ts.set_token(token)
    pro = ts.pro_api()

    # ----- 日期范围 -----
    today_str = datetime.now().strftime("%Y%m%d")

    if args.today:
        start_date = today_str
        end_date = today_str
    elif args.update:
        # 增量：从库里最后日期+1天开始
        conn_tmp = sqlite3.connect(args.db)
        cur_tmp = conn_tmp.cursor()
        cur_tmp.execute("SELECT MAX(trade_date) FROM daily")
        row = cur_tmp.fetchone()
        if row and row[0]:
            start_date = (datetime.strptime(row[0], "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
        else:
            start_date = "20240101"
        conn_tmp.close()
        end_date = today_str
    else:
        start_date = args.start or today_str
        end_date = args.end or today_str

    print("=" * 60)
    print("  Tushare Pro · trade_date 循环下载器")
    print("=" * 60)
    print(f"  📅 日期范围: {start_date} ~ {end_date}")
    print(f"  🔑 Token:    {token[:8]}...{token[-4:]}")
    print(f"  💾 数据库:   {args.db}")
    print(f"  🔄 重试:     {MAX_RETRIES}次, 间隔{RETRY_DELAY}s")
    print()

    # ----- 获取交易日 -----
    print("📊 获取交易日历...", end=" ", flush=True)
    dates = get_trade_dates(pro, start_date, end_date)
    total = len(dates)
    if total == 0:
        print("无交易日，退出")
        return
    print(f"{total} 个交易日")

    # 预估时间：每个交易日约0.5s（含API调用+写入）
    est_seconds = total * 0.5
    print(f"  ⏱ 预计耗时: ~{format_duration(est_seconds)}")
    print()

    # ----- 初始化数据库 -----
    conn = init_db(args.db)
    cur = conn.cursor()

    # ----- 逐日循环下载 -----
    total_rows = 0
    empty_days = 0
    error_days = 0
    start_time = time.time()
    call_count = 0

    for i, date in enumerate(dates):
        # 速率限制：积分有限，别被Tushare封了
        if call_count >= RATE_LIMIT_CALLS:
            wait = RATE_LIMIT_WINDOW + 3
            print(f"\n  ⏳ 速率保护：暂停 {wait}s ...", end="", flush=True)
            time.sleep(wait)
            call_count = 0
            print("继续")

        # 下载
        df = download_one_date(pro, date)
        call_count += 1

        if df is None:
            error_days += 1
            n = 0
        elif df.empty:
            empty_days += 1
            n = 0
        else:
            n = save_batch(cur, df)
            total_rows += n

        # 进度条
        elapsed = time.time() - start_time
        pct = (i + 1) / total * 100
        if i > 0:
            eta = elapsed / (i + 1) * (total - i - 1)
            eta_str = f"剩余~{format_duration(eta)}"
        else:
            eta_str = "计算中..."

        bar = (
            f"\r  [{i+1:>4}/{total}] {pct:5.1f}% | {date} | "
            f"+{n:>4}条 | {total_rows:>10,}累计 | {eta_str}"
        )
        print(bar + " " * 8, end="", flush=True)

        # 定期提交数据库
        if (i + 1) % COMMIT_EVERY == 0:
            conn.commit()

        time.sleep(CALL_GAP)

    # 最终提交
    conn.commit()

    total_t = time.time() - start_time
    print("\n")
    print("=" * 60)
    print(f"  ✅ 完成！")
    print(f"  📊 总记录: {total_rows:,} 条")
    print(f"  📅 交易日: {total} 个 (空数据{empty_days}/失败{error_days})")
    print(f"  ⏱ 总耗时: {format_duration(total_t)}")
    if total_rows > 0:
        avg = total_t / total_rows * 1000
        print(f"  ⚡ 均速:   {avg:.1f}ms/条")
    print("=" * 60)

    # 数据库统计
    cur.execute("SELECT COUNT(DISTINCT ts_code) FROM daily")
    stock_count = cur.fetchone()[0]
    cur.execute("SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM daily")
    d_min, d_max, d_total = cur.fetchone()
    print(f"\n📈 数据库概览:")
    print(f"  股票数:   {stock_count}")
    print(f"  日期范围: {d_min} ~ {d_max}")
    print(f"  总记录数: {d_total:,}")
    print()

    conn.close()


if __name__ == "__main__":
    main()
