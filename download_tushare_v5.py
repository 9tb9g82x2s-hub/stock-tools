#!/usr/bin/env python3
"""
A股全市场日线数据下载器 v5（Tushare Pro · trade_date循环 · 三引擎）

核心理念：按交易日循环，不按股票代码循环（220天 vs 5000+只）

引擎选择：
  - SQLite  默认，零配置，快速验证
  - MySQL   关系型，长期存储，SQL查询灵活
  - MongoDB 文档型，schema灵活，适合非结构化扩展

用法：
  # SQLite（默认）
  /usr/bin/python3 ~/stock-tools/download_tushare_v5.py --update

  # MySQL
  /usr/bin/python3 ~/stock-tools/download_tushare_v5.py \
      --db mysql+pymysql://root:密码@localhost:3306/stock_db --update

  # MongoDB
  /usr/bin/python3 ~/stock-tools/download_tushare_v5.py \
      --db mongodb://root:密码@localhost:27017/stock_db --update

前置依赖：
  pip3 install --user tushare sqlalchemy pymysql pymongo

Token配置：
  export TUSHARE_TOKEN=你的token
  # 或 echo 'token' > ~/.tushare_token
  # 或 --token 参数
"""

import os, sys, time, argparse
from datetime import datetime, timedelta

# ============ 配置 ============

TOKEN = os.environ.get("TUSHARE_TOKEN", "")
TOKEN_FILE = os.path.expanduser("~/.tushare_token")
if not TOKEN and os.path.exists(TOKEN_FILE):
    with open(TOKEN_FILE) as f:
        TOKEN = f.read().strip()

DEFAULT_DB = os.path.expanduser("~/stock-data/tushare_daily.db")
MAX_RETRIES = 3
RETRY_DELAY = 1
RATE_LIMIT_CALLS = 190
RATE_LIMIT_WINDOW = 60
COMMIT_EVERY = 20
CALL_GAP = 0.05


# ============ 表结构定义 ============

# SQLite DDL
SQLITE_DDL = """
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

# MySQL DDL（优化：VARCHAR代替TEXT，DOUBLE代替REAL，加COMMENT）
MYSQL_DDL = """
CREATE TABLE IF NOT EXISTS `daily` (
    `ts_code`    VARCHAR(12)  NOT NULL  COMMENT '股票代码',
    `trade_date` CHAR(8)      NOT NULL  COMMENT '交易日期',
    `open`       DOUBLE       DEFAULT NULL COMMENT '开盘价',
    `high`       DOUBLE       DEFAULT NULL COMMENT '最高价',
    `low`        DOUBLE       DEFAULT NULL COMMENT '最低价',
    `close`      DOUBLE       DEFAULT NULL COMMENT '收盘价',
    `pre_close`  DOUBLE       DEFAULT NULL COMMENT '昨收价',
    `change`     DOUBLE       DEFAULT NULL COMMENT '涨跌额',
    `pct_chg`    DOUBLE       DEFAULT NULL COMMENT '涨跌幅',
    `vol`        DOUBLE       DEFAULT NULL COMMENT '成交量(手)',
    `amount`     DOUBLE       DEFAULT NULL COMMENT '成交额(千元)',
    PRIMARY KEY (`ts_code`, `trade_date`),
    INDEX `idx_trade_date` (`trade_date`),
    INDEX `idx_ts_code`    (`ts_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='A股日线行情数据';
"""

# 字段列表（三个引擎共用）
FIELD_NAMES = [
    "ts_code", "trade_date", "open", "high", "low", "close",
    "pre_close", "change", "pct_chg", "vol", "amount"
]


# ============ DatabaseEngine 统一接口 ============

class DatabaseEngine:
    """三引擎统一抽象：SQLite / MySQL / MongoDB"""

    def __init__(self, db_url):
        self.url = db_url
        if db_url.startswith("mongodb"):
            self._init_mongo(db_url)
        elif db_url.startswith("mysql"):
            self._init_mysql(db_url)
        else:
            self._init_sqlite(db_url)

    # ---- SQLite ----
    def _init_sqlite(self, path):
        import sqlite3
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.cur = self.conn.cursor()
        self.cur.executescript(SQLITE_DDL)
        self.conn.commit()
        self._engine = "sqlite"

    def _insert_sqlite(self, records):
        sql = """INSERT OR REPLACE INTO daily
            (ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        self.cur.executemany(sql, records)

    def _stats_sqlite(self):
        self.cur.execute("SELECT COUNT(DISTINCT ts_code) FROM daily")
        stocks = self.cur.fetchone()[0]
        self.cur.execute("SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM daily")
        r = self.cur.fetchone()
        return {"stocks": stocks, "date_min": r[0], "date_max": r[1], "total_rows": r[2]}

    def _last_date_sqlite(self):
        self.cur.execute("SELECT MAX(trade_date) FROM daily")
        r = self.cur.fetchone()
        return r[0] if r and r[0] else None

    def _commit_sqlite(self):
        self.conn.commit()

    def _close_sqlite(self):
        self.conn.commit()
        self.cur.close()
        self.conn.close()

    # ---- MySQL ----
    def _init_mysql(self, url):
        from sqlalchemy import create_engine, text
        self.engine = create_engine(url, pool_size=10, max_overflow=20,
                                     pool_recycle=3600, pool_pre_ping=True)
        with self.engine.begin() as conn:
            for stmt in MYSQL_DDL.split(";"):
                stmt = stmt.strip()
                if stmt:
                    conn.execute(text(stmt))
        self.raw = self.engine.raw_connection()
        self.cur = self.raw.cursor()
        self._engine = "mysql"

    def _insert_mysql(self, records):
        sql = """INSERT INTO daily
            (ts_code, trade_date, open, high, low, close, pre_close, `change`, pct_chg, vol, amount)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                open=VALUES(open), high=VALUES(high), low=VALUES(low), close=VALUES(close),
                pre_close=VALUES(pre_close), `change`=VALUES(`change`),
                pct_chg=VALUES(pct_chg), vol=VALUES(vol), amount=VALUES(amount)"""
        self.cur.executemany(sql, records)

    def _stats_mysql(self):
        self.cur.execute("SELECT COUNT(DISTINCT ts_code) FROM daily")
        stocks = self.cur.fetchone()[0]
        self.cur.execute("SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM daily")
        r = self.cur.fetchone()
        return {"stocks": stocks, "date_min": r[0], "date_max": r[1], "total_rows": r[2]}

    def _last_date_mysql(self):
        self.cur.execute("SELECT MAX(trade_date) FROM daily")
        r = self.cur.fetchone()
        return r[0] if r and r[0] else None

    def _commit_mysql(self):
        self.raw.commit()

    def _close_mysql(self):
        self.raw.commit()
        self.cur.close()
        self.raw.close()

    # ---- MongoDB ----
    def _init_mongo(self, url):
        from pymongo import MongoClient, ASCENDING, ReplaceOne
        # url格式: mongodb://user:pass@host:port/database
        self.mongo_client = MongoClient(url)
        # 从URL中提取数据库名
        db_name = url.rsplit("/", 1)[-1].split("?")[0] or "stock_db"
        self.mongo_db = self.mongo_client[db_name]
        self.mongo_coll = self.mongo_db["daily"]
        # 建唯一索引（ts_code + trade_date复合索引，防止重复）
        self.mongo_coll.create_index(
            [("ts_code", ASCENDING), ("trade_date", ASCENDING)],
            unique=True, name="idx_ts_code_date"
        )
        self.mongo_coll.create_index([("trade_date", ASCENDING)], name="idx_trade_date")
        self._engine = "mongodb"

    def _insert_mongo(self, records):
        """MongoDB批量写入，用bulk_write + ReplaceOne(upsert=True)防重复"""
        from pymongo import ReplaceOne
        operations = []
        for r in records:
            doc = dict(zip(FIELD_NAMES, r))
            # 用ts_code+trade_date做唯一键，已存在则替换
            operations.append(
                ReplaceOne(
                    {"ts_code": doc["ts_code"], "trade_date": doc["trade_date"]},
                    doc,
                    upsert=True
                )
            )
        if operations:
            self.mongo_coll.bulk_write(operations, ordered=False)

    def _stats_mongo(self):
        stocks = len(self.mongo_coll.distinct("ts_code"))
        pipeline = [
            {"$group": {
                "_id": None,
                "date_min": {"$min": "$trade_date"},
                "date_max": {"$max": "$trade_date"},
                "total": {"$sum": 1}
            }}
        ]
        r = list(self.mongo_coll.aggregate(pipeline))
        if r:
            return {"stocks": stocks, "date_min": r[0]["date_min"],
                    "date_max": r[0]["date_max"], "total_rows": r[0]["total"]}
        return {"stocks": 0, "date_min": None, "date_max": None, "total_rows": 0}

    def _last_date_mongo(self):
        doc = self.mongo_coll.find_one(
            {}, sort=[("trade_date", -1)], projection={"trade_date": 1}
        )
        return doc["trade_date"] if doc else None

    def _commit_mongo(self):
        pass  # MongoDB每次写入即生效，无需显式commit

    def _close_mongo(self):
        self.mongo_client.close()

    # ---- 统一调度 ----
    def insert_batch(self, records):
        getattr(self, f"_insert_{self._engine}")(records)

    def commit(self):
        getattr(self, f"_commit_{self._engine}")()

    def get_last_date(self):
        return getattr(self, f"_last_date_{self._engine}")()

    def get_stats(self):
        return getattr(self, f"_stats_{self._engine}")()

    def close(self):
        getattr(self, f"_close_{self._engine}")()

    @property
    def engine_name(self):
        names = {"sqlite": "SQLite", "mysql": "MySQL", "mongodb": "MongoDB"}
        return names.get(self._engine, self._engine)


# ============ Tushare 接口 ============

def get_trade_dates(pro, start_date, end_date):
    df = pro.trade_cal(exchange='SSE', start_date=start_date,
                       end_date=end_date, is_open='1')
    return sorted(df['cal_date'].tolist())


def download_one_date(pro, trade_date):
    for attempt in range(MAX_RETRIES):
        try:
            return pro.daily(trade_date=trade_date)
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
            else:
                print(f"\n  ⚠ {trade_date} 下载失败(重试{MAX_RETRIES}次): {e}")
                return None
    return None


def df_to_records(df):
    records = []
    for _, row in df.iterrows():
        try:
            records.append(tuple(
                str(row.get(f, "")) if f in ("ts_code", "trade_date")
                else float(row.get(f, 0) or 0)
                for f in FIELD_NAMES
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


def mask_url(url):
    """隐藏密码"""
    if "@" not in url:
        return url
    parts = url.split("@")
    cred = parts[0].split(":") if ":" in parts[0] else [parts[0], "***"]
    return f"{cred[0]}:***@{'@'.join(parts[1:])}"


# ============ 主流程 ============

def main():
    parser = argparse.ArgumentParser(
        description="Tushare Pro · trade_date循环 · 三引擎下载器 v5",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
引擎URL格式:
  SQLite:   ~/stock-data/tushare_daily.db                (默认)
  MySQL:    mysql+pymysql://user:pass@host:3306/dbname
  MongoDB:  mongodb://user:pass@host:27017/dbname

示例:
  %(prog)s --update                                       # SQLite增量
  %(prog)s --db mysql+pymysql://root:pwd@localhost/stock --update
  %(prog)s --db mongodb://root:pwd@localhost/stock --update
  %(prog)s -s 20240101 -e 20241231                        # 指定范围
        """
    )
    parser.add_argument("--db", type=str, default=DEFAULT_DB,
                        help=f"数据库连接URL (默认: {DEFAULT_DB})")
    parser.add_argument("--start", "-s", type=str, help="开始日期 YYYYMMDD")
    parser.add_argument("--end", "-e", type=str, help="结束日期 YYYYMMDD")
    parser.add_argument("--update", "-u", action="store_true", help="增量更新")
    parser.add_argument("--today", action="store_true", help="仅下载今天")
    parser.add_argument("--token", "-t", type=str, help="Tushare Token")
    args = parser.parse_args()

    # Token
    token = args.token or TOKEN
    if not token:
        print("❌ 未配置Token")
        print("   1. export TUSHARE_TOKEN=你的token")
        print("   2. echo 'token' > ~/.tushare_token")
        print("   3. --token 参数")
        sys.exit(1)

    import tushare as ts
    ts.set_token(token)
    pro = ts.pro_api()

    # 数据库
    db = DatabaseEngine(args.db)
    today_str = datetime.now().strftime("%Y%m%d")

    # 日期范围
    if args.today:
        start_date = end_date = today_str
    elif args.update:
        last = db.get_last_date()
        start_date = (datetime.strptime(last, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d") if last else "20240101"
        end_date = today_str
    else:
        start_date = args.start or today_str
        end_date = args.end or today_str

    print("=" * 60)
    print("  Tushare Pro · trade_date 循环下载器 v5")
    print("=" * 60)
    print(f"  🗄  引擎: {db.engine_name}  →  {mask_url(args.db)}")
    print(f"  📅 日期: {start_date} ~ {end_date}")
    print(f"  🔑 Token: {token[:8]}...{token[-4:]}")
    print(f"  🔄 重试: {MAX_RETRIES}次, 间隔{RETRY_DELAY}s")
    print()

    print("📊 获取交易日历...", end=" ", flush=True)
    dates = get_trade_dates(pro, start_date, end_date)
    total = len(dates)
    if total == 0:
        print("无交易日，退出"); db.close(); return
    print(f"{total} 个交易日, 预计~{format_duration(total*0.5)}")
    print()

    total_rows = 0; empty_days = 0; error_days = 0
    start_time = time.time(); call_count = 0

    for i, date in enumerate(dates):
        if call_count >= RATE_LIMIT_CALLS:
            wait = RATE_LIMIT_WINDOW + 3
            print(f"\n  ⏳ 限速暂停 {wait}s ...", end="", flush=True)
            time.sleep(wait); call_count = 0; print("继续")

        df = download_one_date(pro, date); call_count += 1

        if df is None:
            error_days += 1; n = 0
        elif df.empty:
            empty_days += 1; n = 0
        else:
            records = df_to_records(df)
            if records:
                db.insert_batch(records)
                n = len(records); total_rows += n
            else:
                n = 0

        elapsed = time.time() - start_time
        pct = (i + 1) / total * 100
        eta = format_duration(elapsed/(i+1)*(total-i-1)) if i > 0 else "..."

        print(f"\r  [{i+1:>4}/{total}] {pct:5.1f}% | {date} | +{n:>4}条"
              f" | {total_rows:>10,}累计 | 剩余~{eta}   ",
              end="", flush=True)

        if (i + 1) % COMMIT_EVERY == 0:
            db.commit()
        time.sleep(CALL_GAP)

    db.commit()
    total_t = time.time() - start_time

    print("\n\n" + "=" * 60)
    print(f"  ✅ 完成！")
    print(f"  📊 新增: {total_rows:,}条 | 空{empty_days}/失败{error_days}")
    print(f"  ⏱  耗时: {format_duration(total_t)}")
    if total_rows > 0:
        print(f"  ⚡ 均速: {total_t/total_rows*1000:.1f}ms/条")
    print("=" * 60)

    stats = db.get_stats()
    print(f"\n📈 {db.engine_name} 数据库概览:")
    print(f"  股票数:   {stats['stocks']}")
    print(f"  日期范围: {stats['date_min']} ~ {stats['date_max']}")
    print(f"  总记录数: {stats['total_rows']:,}")
    print()

    db.close()


if __name__ == "__main__":
    main()
