#!/usr/bin/env python3
"""
A股全市场日线数据下载器 v4（Tushare Pro · trade_date循环 · MySQL/SQLite双引擎）

核心理念：
  - 按交易日循环，不按股票代码循环（220天 vs 5000+只）
  - 数据进MySQL用于长期存储，SQLite用于快速验证

用法：
  # SQLite（默认，零配置）
  /usr/bin/python3 ~/stock-tools/download_tushare_v4.py --update

  # MySQL（一次配置，持久使用）
  /usr/bin/python3 ~/stock-tools/download_tushare_v4.py \
      --db mysql+pymysql://root:密码@localhost:3306/stock_db \
      -s 20240101 -e 20241231

  # 首次用MySQL需建库：
  #   mysql -u root -p -e "CREATE DATABASE stock_db CHARACTER SET utf8mb4;"

前置依赖：
  pip3 install --user tushare sqlalchemy pymysql

Token配置（三选一）：
  1. export TUSHARE_TOKEN=你的token
  2. echo '你的token' > ~/.tushare_token
  3. --token 参数

参考：关注公众号"挖地兔"，发送"mysql"获取完整参考代码
"""

import os, sys, sqlite3, time, argparse, json
from datetime import datetime, timedelta

# ============ 配置 ============

TOKEN = os.environ.get("TUSHARE_TOKEN", "")
TOKEN_FILE = os.path.expanduser("~/.tushare_token")
if not TOKEN and os.path.exists(TOKEN_FILE):
    with open(TOKEN_FILE) as f:
        TOKEN = f.read().strip()

DEFAULT_DB = os.path.expanduser("~/stock-data/tushare_daily.db")   # SQLite
MAX_RETRIES = 3
RETRY_DELAY = 1
RATE_LIMIT_CALLS = 190       # 120积分: 200次/分钟, 留10次余量
RATE_LIMIT_WINDOW = 60
COMMIT_EVERY = 20            # 每N天commit一次
CALL_GAP = 0.05              # 调用间隔(秒)


# ============ 优化后的表结构 ============
# 跟df.to_sql()默认建表不同，这里预定义了最优字段类型和索引

DAILY_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS `daily` (
    `ts_code`    VARCHAR(12)  NOT NULL  COMMENT '股票代码 (如 000001.SZ)',
    `trade_date` CHAR(8)      NOT NULL  COMMENT '交易日期 (YYYYMMDD)',
    `open`       DOUBLE       DEFAULT NULL COMMENT '开盘价',
    `high`       DOUBLE       DEFAULT NULL COMMENT '最高价',
    `low`        DOUBLE       DEFAULT NULL COMMENT '最低价',
    `close`      DOUBLE       DEFAULT NULL COMMENT '收盘价',
    `pre_close`  DOUBLE       DEFAULT NULL COMMENT '昨收价',
    `change`     DOUBLE       DEFAULT NULL COMMENT '涨跌额',
    `pct_chg`    DOUBLE       DEFAULT NULL COMMENT '涨跌幅(%)',
    `vol`        DOUBLE       DEFAULT NULL COMMENT '成交量(手)',
    `amount`     DOUBLE       DEFAULT NULL COMMENT '成交额(千元)',
    PRIMARY KEY (`ts_code`, `trade_date`),
    INDEX `idx_trade_date` (`trade_date`),
    INDEX `idx_ts_code`    (`ts_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='A股日线行情数据 — Tushare Pro daily接口';
"""

# 建表（SQLite版）
DAILY_TABLE_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS daily (
    ts_code    TEXT    NOT NULL,
    trade_date TEXT    NOT NULL,
    open       REAL,
    high       REAL,
    low        REAL,
    close      REAL,
    pre_close  REAL,
    change     REAL,
    pct_chg    REAL,
    vol        REAL,
    amount     REAL,
    PRIMARY KEY (ts_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_daily_date ON daily(trade_date);
CREATE INDEX IF NOT EXISTS idx_daily_code ON daily(ts_code);
"""

INSERT_SQL = """INSERT OR REPLACE INTO daily
    (ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

# MySQL版 INSERT ON DUPLICATE KEY UPDATE
INSERT_SQL_MYSQL = """INSERT INTO daily
    (ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        open=VALUES(open), high=VALUES(high), low=VALUES(low), close=VALUES(close),
        pre_close=VALUES(pre_close), `change`=VALUES(`change`),
        pct_chg=VALUES(pct_chg), vol=VALUES(vol), amount=VALUES(amount)"""


# ============ 数据库引擎 ============

class DatabaseEngine:
    """统一的数据库接口，屏蔽SQLite/MySQL差异"""

    def __init__(self, db_url):
        self.is_mysql = db_url.startswith("mysql")
        self.db_url = db_url

        if self.is_mysql:
            from sqlalchemy import create_engine, text
            # 增大连接池，应对高频写入
            self.engine = create_engine(
                db_url,
                pool_size=10,
                max_overflow=20,
                pool_recycle=3600,
                pool_pre_ping=True,
            )
            self.raw_conn = self.engine.raw_connection()
            self.cur = self.raw_conn.cursor()
            # 建表
            with self.engine.begin() as conn:
                for stmt in DAILY_TABLE_DDL.split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        conn.execute(text(stmt))
        else:
            # SQLite
            os.makedirs(os.path.dirname(db_url), exist_ok=True)
            self.raw_conn = sqlite3.connect(db_url)
            self.cur = self.raw_conn.cursor()
            self.cur.executescript(DAILY_TABLE_DDL_SQLITE)

    def insert_batch(self, records):
        """批量写入"""
        if self.is_mysql:
            self.cur.executemany(INSERT_SQL_MYSQL, records)
        else:
            self.cur.executemany(INSERT_SQL, records)

    def commit(self):
        self.raw_conn.commit()

    def get_last_date(self):
        """获取数据库中最新日期"""
        if self.is_mysql:
            self.cur.execute("SELECT MAX(trade_date) FROM daily")
        else:
            self.cur.execute("SELECT MAX(trade_date) FROM daily")
        row = self.cur.fetchone()
        return row[0] if row and row[0] else None

    def get_stats(self):
        """获取数据库统计"""
        stats = {}
        if self.is_mysql:
            self.cur.execute("SELECT COUNT(DISTINCT ts_code) FROM daily")
            stats['stocks'] = self.cur.fetchone()[0]
            self.cur.execute("SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM daily")
            row = self.cur.fetchone()
        else:
            self.cur.execute("SELECT COUNT(DISTINCT ts_code) FROM daily")
            stats['stocks'] = self.cur.fetchone()[0]
            self.cur.execute("SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM daily")
            row = self.cur.fetchone()
        stats['date_min'] = row[0]
        stats['date_max'] = row[1]
        stats['total_rows'] = row[2]
        return stats

    def close(self):
        self.commit()
        self.cur.close()
        self.raw_conn.close()


# ============ Tushare 接口 ============

def get_trade_dates(pro, start_date, end_date):
    """获取交易日历"""
    df = pro.trade_cal(
        exchange='SSE',
        start_date=start_date,
        end_date=end_date,
        is_open='1'
    )
    return sorted(df['cal_date'].tolist())


def download_one_date(pro, trade_date):
    """下载单个交易日全市场日线，带重试机制"""
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


def df_to_records(df):
    """将DataFrame转为写入用的元组列表"""
    records = []
    for _, row in df.iterrows():
        try:
            records.append((
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
            ))
        except (ValueError, TypeError, KeyError):
            pass
    return records


def format_duration(seconds):
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
        description="Tushare Pro · trade_date循环 · MySQL/SQLite双引擎",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # SQLite 增量更新（默认，零配置）
  %(prog)s --update

  # MySQL 首次全量
  %(prog)s --db mysql+pymysql://root:密码@localhost:3306/stock_db -s 20240101 -e 20241231

  # MySQL 增量更新
  %(prog)s --db mysql+pymysql://root:密码@localhost:3306/stock_db --update

  # 仅下载今天
  %(prog)s --today
        """
    )
    parser.add_argument("--db", type=str, default=DEFAULT_DB,
                        help=f"数据库连接。SQLite文件路径 或 MySQL URL。\n"
                             f"  默认: {DEFAULT_DB}\n"
                             f"  MySQL格式: mysql+pymysql://用户:密码@主机:端口/库名")
    parser.add_argument("--start", "-s", type=str, help="开始日期 YYYYMMDD")
    parser.add_argument("--end", "-e", type=str, help="结束日期 YYYYMMDD")
    parser.add_argument("--update", "-u", action="store_true", help="增量更新模式")
    parser.add_argument("--today", action="store_true", help="仅下载今天")
    parser.add_argument("--token", "-t", type=str, help="Tushare Token")
    args = parser.parse_args()

    # ----- Token -----
    token = args.token or TOKEN
    if not token:
        print("❌ 未配置Token，请通过以下任一方式提供：")
        print("   1. export TUSHARE_TOKEN=你的token")
        print("   2. echo '你的token' > ~/.tushare_token")
        print("   3. --token 参数")
        print("\n📌 没有Token？去 https://tushare.pro 注册，个人主页拿Token")
        sys.exit(1)

    # ----- 初始化 Tushare Pro -----
    import tushare as ts
    ts.set_token(token)
    pro = ts.pro_api()

    # ----- 判断引擎类型 -----
    is_mysql = args.db.startswith("mysql")
    engine_type = "MySQL" if is_mysql else "SQLite"

    # ----- 数据库引擎 -----
    db = DatabaseEngine(args.db)

    # ----- 日期范围 -----
    today_str = datetime.now().strftime("%Y%m%d")

    if args.today:
        start_date = today_str
        end_date = today_str
    elif args.update:
        last = db.get_last_date()
        start_date = (datetime.strptime(last, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d") if last else "20240101"
        end_date = today_str
    else:
        start_date = args.start or today_str
        end_date = args.end or today_str

    print("=" * 60)
    print("  Tushare Pro · trade_date 循环下载器 v4")
    print("=" * 60)
    print(f"  🗄  存储引擎: {engine_type}")
    if is_mysql:
        # 隐藏密码
        safe_url = args.db
        if "@" in safe_url:
            parts = safe_url.split("@")
            cred_part = parts[0].split(":") if ":" in parts[0] else [parts[0], "***"]
            safe_url = f"{cred_part[0]}:***@{'@'.join(parts[1:])}"
        print(f"    {safe_url}")
    else:
        print(f"    {args.db}")
    print(f"  📅 日期范围: {start_date} ~ {end_date}")
    print(f"  🔑 Token:    {token[:8]}...{token[-4:]}")
    print(f"  🔄 重试:     {MAX_RETRIES}次, 间隔{RETRY_DELAY}s")
    print()

    # ----- 获取交易日 -----
    print("📊 获取交易日历...", end=" ", flush=True)
    dates = get_trade_dates(pro, start_date, end_date)
    total = len(dates)
    if total == 0:
        print("无交易日，退出")
        db.close()
        return
    print(f"{total} 个交易日")
    print(f"  ⏱  预计耗时: ~{format_duration(total * 0.5)}")
    print()

    # ----- 逐日循环下载 -----
    total_rows = 0
    empty_days = 0
    error_days = 0
    start_time = time.time()
    call_count = 0

    for i, date in enumerate(dates):
        # 速率保护
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
            records = df_to_records(df)
            if records:
                db.insert_batch(records)
                n = len(records)
                total_rows += n
            else:
                n = 0

        # 进度条
        elapsed = time.time() - start_time
        pct = (i + 1) / total * 100
        eta_str = f"剩余~{format_duration(elapsed / (i+1) * (total - i - 1))}" if i > 0 else "计算中..."

        bar = (
            f"\r  [{i+1:>4}/{total}] {pct:5.1f}% | {date} | "
            f"+{n:>4}条 | {total_rows:>10,}累计 | {eta_str}"
        )
        print(bar + " " * 8, end="", flush=True)

        # 定期提交
        if (i + 1) % COMMIT_EVERY == 0:
            db.commit()

        time.sleep(CALL_GAP)

    db.commit()
    total_t = time.time() - start_time

    print("\n")
    print("=" * 60)
    print(f"  ✅ 完成！")
    print(f"  📊 新增记录: {total_rows:,} 条")
    print(f"  📅 交易日:   {total} 个 (空数据{empty_days}/失败{error_days})")
    print(f"  ⏱  总耗时:   {format_duration(total_t)}")
    if total_rows > 0:
        print(f"  ⚡ 均速:     {total_t/total_rows*1000:.1f}ms/条")
    print("=" * 60)

    # 数据库统计
    stats = db.get_stats()
    print(f"\n📈 {engine_type} 数据库概览:")
    print(f"  股票数:   {stats['stocks']}")
    print(f"  日期范围: {stats['date_min']} ~ {stats['date_max']}")
    print(f"  总记录数: {stats['total_rows']:,}")
    print()

    db.close()


if __name__ == "__main__":
    main()
