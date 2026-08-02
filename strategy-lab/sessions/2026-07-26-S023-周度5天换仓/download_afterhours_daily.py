#!/usr/bin/env python3
"""
每日盘后固定价格交易数据下载脚本
数据源：东方财富妙想API（mx-data）
存入 stock_all.db 的 afterhours_daily 表
每天18:00后执行一次，下载前一日盘后数据
"""
import os, sys, time, sqlite3, json
import requests

API_URL = "https://mkapi2.dfcfs.com/finskillshub/api/claw/query"
API_KEY = os.getenv("MX_APIKEY")
DB_PATH = "/Users/ziruzhu/stock-data/stock_all.db"
BATCH_SIZE = 15  # 每批查询的股票数
SLEEP_BETWEEN = 2  # 批次间等待秒数

if not API_KEY:
    print("ERROR: MX_APIKEY 环境变量未设置")
    sys.exit(1)

def call_mx(query: str) -> list:
    """调用mx-data API，返回 [(ts_code, ah_vol, ah_amount), ...]"""
    headers = {"Content-Type": "application/json", "apikey": API_KEY}
    resp = requests.post(API_URL, headers=headers, json={"toolQuery": query}, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    
    results = []
    try:
        dtos = data["data"]["data"]["searchDataResultDTO"]["dataTableDTOList"]
    except (KeyError, TypeError):
        return results
    
    for dto in dtos:
        code = dto.get("code", "")
        raw = dto.get("rawTable", {})
        name_map = dto.get("nameMap", {})
        
        # 找到盘后成交量和成交额的字段编码
        vol_code = None
        amt_code = None
        for k, v in name_map.items():
            if v == "盘后成交量":
                vol_code = k
            elif v == "盘后成交额":
                amt_code = k
        
        if vol_code and amt_code:
            vol_val = float(raw.get(vol_code, [0])[0]) if raw.get(vol_code) else 0
            amt_val = float(raw.get(amt_code, [0])[0]) if raw.get(amt_code) else 0
            # vol单位是股，转为手；amt单位是元，转为万元
            results.append((code, vol_val / 100, amt_val / 10000))
        else:
            results.append((code, 0, 0))
    
    return results

def create_table():
    """创建afterhours_daily表"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS afterhours_daily (
            ts_code TEXT,
            trade_date TEXT,
            ah_vol REAL,
            ah_amount REAL,
            PRIMARY KEY (ts_code, trade_date)
        )
    """)
    conn.commit()
    conn.close()

def get_stock_list():
    """获取全A股列表（排除北交所）"""
    conn = sqlite3.connect(DB_PATH)
    df = conn.execute("""
        SELECT ts_code, name FROM stock_list 
        WHERE ts_code NOT LIKE '%.BJ'
        ORDER BY ts_code
    """).fetchall()
    conn.close()
    return df

def get_last_date():
    """获取最新的盘后数据日期"""
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT MAX(trade_date) FROM afterhours_daily").fetchone()
    conn.close()
    return row[0] if row[0] else None

def download_date(date_str: str):
    """下载指定日期的全市场盘后数据"""
    stocks = get_stock_list()
    print(f"全A股: {len(stocks)} 只, 目标日期: {date_str}")
    
    total = len(stocks)
    results = []
    
    for i in range(0, total, BATCH_SIZE):
        batch = stocks[i:i+BATCH_SIZE]
        names = " ".join([s[1] for s in batch])  # 股票名称
        query = f"{date_str} {names} 盘后成交量 盘后成交额"
        
        try:
            batch_results = call_mx(query)
            for (code, name), (_, vol, amt) in zip(batch, batch_results):
                results.append((code, date_str, vol, amt))
            
            progress = min(i + BATCH_SIZE, total)
            has_data = sum(1 for r in batch_results if r[1] > 0)
            print(f"  [{progress}/{total}] 完成, {has_data}/{len(batch)}只有盘后数据")
        except Exception as e:
            print(f"  [{i}/{total}] 失败: {e}")
            # 失败时标记为0
            for code, name in batch:
                results.append((code, date_str, 0, 0))
        
        time.sleep(SLEEP_BETWEEN)
    
    # 写入数据库
    conn = sqlite3.connect(DB_PATH)
    conn.executemany(
        "INSERT OR REPLACE INTO afterhours_daily (ts_code, trade_date, ah_vol, ah_amount) VALUES (?, ?, ?, ?)",
        results
    )
    conn.commit()
    
    has_count = sum(1 for r in results if r[2] > 0)
    total_vol = sum(r[2] for r in results)
    print(f"\n写入完成: {len(results)} 条, {has_count}只有盘后成交")
    print(f"盘后总成交量: {total_vol:.0f} 手")
    conn.close()

if __name__ == "__main__":
    import datetime
    
    create_table()
    
    # 默认下载昨天的数据（盘后数据次日才能获取）
    if len(sys.argv) > 1:
        date_str = sys.argv[1]
    else:
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        date_str = yesterday.strftime("%Y-%m-%d")
    
    last = get_last_date()
    if last and last >= date_str.replace("-", ""):
        print(f"日期 {date_str} 已下载过 (最新={last})")
        sys.exit(0)
    
    print(f"开始下载 {date_str} 的盘后数据...")
    t0 = time.time()
    download_date(date_str)
    print(f"总耗时: {time.time()-t0:.0f}s")
