#!/usr/bin/env python3
"""
舆情大规模采集脚本 v3
- 分页拉取全部公告+研报（不限于最近N条）
- 断点续跑（checkpoint机制）
- 支持三层股票池：core / active / all
- 限速控制，避免触发反爬

用法：
  python3 news_collector_v3.py --pool core    # 核心池（全部历史）
  python3 news_collector_v3.py --pool active  # 活跃池（有研报覆盖的）
  python3 news_collector_v3.py --pool all     # 全市场（仅公告）
  python3 news_collector_v3.py --resume       # 从断点继续
  python3 news_collector_v3.py --stock 300750.SZ  # 单只全量
"""

import sqlite3
import os
import sys
import json
import argparse
import subprocess
import re
import time
import signal
from datetime import datetime, timedelta
from pathlib import Path

# ========== 配置 ==========
DB_PATH = os.path.expanduser("~/stock-data/stock_all.db")
SCRIPT_DIR = os.path.expanduser("~/stock-tools")
CHECKPOINT_FILE = os.path.join(SCRIPT_DIR, "news_collector_checkpoint.json")
PAGE_SIZE = 50         # 每页条数（westockdata单页上限）
DELAY_BETWEEN_STOCKS = 1.5   # 每只股票间隔（秒）
DELAY_BETWEEN_PAGES = 0.3    # 每页间隔（秒）
MAX_RETRIES = 2               # 单只股票重试次数

# L1 核心池：你的持仓 + 重点关注
CORE_POOL = [
    '300750.SZ',  # 宁德时代
    '002475.SZ',  # 立讯精密
    '300661.SZ',  # 圣邦股份
    '002049.SZ',  # 紫光国微
    '300059.SZ',  # 东方财富
    '002236.SZ',  # 大华股份
    '603986.SH',  # 兆易创新
    '600522.SH',  # 中天科技
    '603160.SH',  # 汇顶科技
    '688981.SH',  # 中芯国际
    '300394.SZ',  # 天孚通信
    '600745.SH',  # 闻泰科技
    '600703.SH',  # 三安光电
    '002371.SZ',  # 北方华创
    '688012.SH',  # 中微公司
    '300782.SZ',  # 卓胜微
    '603501.SH',  # 韦尔股份
    '002916.SZ',  # 深南电路
    '300502.SZ',  # 新易盛
    '688256.SH',  # 寒武纪
    '002230.SZ',  # 科大讯飞
    '688111.SH',  # 金山办公
    '000063.SZ',  # 中兴通讯
    '600050.SH',  # 中国联通
    '002415.SZ',  # 海康威视
    '601138.SH',  # 工业富联
    '600519.SH',  # 贵州茅台（参考）
    '000858.SZ',  # 五粮液（参考）
    '601318.SH',  # 中国平安（参考）
    '000001.SZ',  # 平安银行（参考）
]

shutdown_flag = False

def signal_handler(sig, frame):
    global shutdown_flag
    print("\n[INFO] 收到中断信号，正在安全退出...")
    shutdown_flag = True
    save_checkpoint({})  # 保存当前checkpoint

signal.signal(signal.SIGINT, signal_handler)


# ========== 工具函数 ==========

def ts_code_to_westock(ts_code):
    parts = ts_code.split('.')
    if len(parts) == 2:
        return parts[1].lower() + parts[0]
    return ts_code.lower()


def parse_markdown_table(text):
    """解析 markdown 表格（自动跳过统计行）"""
    lines = text.strip().split('\n')
    
    header_idx = -1
    for i, line in enumerate(lines):
        if line.strip().startswith('|') and line.count('|') >= 3:
            if i + 1 < len(lines) and '---' in lines[i + 1]:
                header_idx = i
                break
    
    if header_idx == -1:
        return []
    
    headers = [h.strip() for h in lines[header_idx].split('|')[1:-1]]
    data_lines = lines[header_idx + 2:]
    
    result = []
    for line in data_lines:
        if not line.strip() or not line.strip().startswith('|'):
            continue
        cells = [c.strip() for c in line.split('|')[1:-1]]
        if len(cells) == len(headers):
            result.append(dict(zip(headers, cells)))
    return result


def calc_sentiment_rule(title):
    text = title.lower()
    pos_kw = ['业绩', '中标', '合作', '增长', '盈利', '分红', '授权', '获批', '扩产', '订单', '升级', '回购', '增持']
    neg_kw = ['亏损', '处罚', '诉讼', '风险', '下滑', '违规', '调查', '停牌', '债务', '减持', '质押', '更正', '问询']
    pos = sum(1 for kw in pos_kw if kw in text)
    neg = sum(1 for kw in neg_kw if kw in text)
    total = pos + neg
    return (pos - neg) / total if total > 0 else 0.0


def calc_sentiment_from_rating(rating, title=""):
    """研报情绪：优先用投资评级，评级为空时fallback到标题关键词"""
    rating_lower = rating.lower().strip() if rating else ""
    
    rating_map = {
        '买入': 0.8, 'buy': 0.8, '强烈推荐': 0.9, '强推': 0.8,
        '增持': 0.5, 'accumulate': 0.5, '推荐': 0.6, '买进': 0.7,
        '中性': 0.0, 'hold': 0.0, '持有': 0.0,
        '减持': -0.5, 'reduce': -0.5,
        '卖出': -0.8, 'sell': -0.8,
    }
    
    # 优先用评级字段
    for key, score in rating_map.items():
        if key and key in rating_lower:
            return score, rating_lower
    
    # 评级为空，用标题关键词推断
    if title:
        text = title.lower()
        pos_kw = ['买入', '增持', '强推', '推荐', '买进', '看好', '超预期', '增长', '业绩高增']
        neg_kw = ['减持', '卖出', '中性', '风险', '下滑', '低于预期', '亏损', '谨慎']
        pos = sum(1 for kw in pos_kw if kw in text)
        neg = sum(1 for kw in neg_kw if kw in text)
        if pos + neg > 0:
            return round((pos - neg) / (pos + neg), 2), 'inferred'
    
    return 0.0, ""


def get_total_pages(output):
    """从westockdata输出中提取总条数"""
    match = re.search(r'共\s*(\d+)\s*条', output)
    if match:
        total = int(match.group(1))
        pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
        return total, pages
    return 0, 0


def call_westock(cmd, retries=MAX_RETRIES):
    """调用westockdata，带重试"""
    for attempt in range(retries):
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
            if result.returncode == 0:
                return result.stdout
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
        except subprocess.TimeoutExpired:
            if attempt < retries - 1:
                time.sleep(3)
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2)
    return None


# ========== Checkpoint 管理 ==========

def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE) as f:
            return json.load(f)
    return {
        'completed_stocks': [],    # 已完成的股票
        'current_stock': None,     # 当前正在处理的股票
        'current_page_notice': 0,  # 公告当前页
        'current_page_report': 0,  # 研报当前页
        'pool': 'core',
        'last_updated': ''
    }


def save_checkpoint(cp):
    cp['last_updated'] = datetime.now().isoformat()
    os.makedirs(os.path.dirname(CHECKPOINT_FILE), exist_ok=True)
    with open(CHECKPOINT_FILE, 'w') as f:
        json.dump(cp, f, indent=2, ensure_ascii=False)


# ========== 数据入库 ==========

def insert_notice(item, ts_code, conn):
    """插入单条公告"""
    cur = conn.cursor()
    notice_id = f"westock_notice_{item.get('id', '')}"
    pub_time = item.get('time', '')
    title = item.get('title', '')
    
    try:
        cur.execute("""
            INSERT OR IGNORE INTO news 
            (news_id, pub_time, title, source, type, raw_data)
            VALUES (?, ?, ?, 'westock', 'announcement', ?)
        """, (notice_id, pub_time, title, json.dumps(item, ensure_ascii=False)))
        
        score = calc_sentiment_rule(title)
        cur.execute("""
            INSERT OR IGNORE INTO sentiment (news_id, score, method, model_version)
            VALUES (?, ?, 'rule', 'v1.0')
        """, (notice_id, score))
        
        cur.execute("""
            INSERT OR IGNORE INTO news_stock (news_id, ts_code, mention_type)
            VALUES (?, ?, 'direct')
        """, (notice_id, ts_code))
        
        return True
    except Exception as e:
        return False


def insert_report(item, ts_code, conn):
    """插入单条研报"""
    cur = conn.cursor()
    report_id = f"westock_report_{item.get('id', '')}"
    pub_time = item.get('time', '')
    title = item.get('title', '')
    rating = item.get('tzpj', '')
    report_type = item.get('typeStr', '')
    
    try:
        cur.execute("""
            INSERT OR IGNORE INTO news 
            (news_id, pub_time, title, source, type, raw_data)
            VALUES (?, ?, ?, 'westock', 'report', ?)
        """, (report_id, pub_time, title, json.dumps(item, ensure_ascii=False)))
        
        score, effective_rating = calc_sentiment_from_rating(rating, title)
        keywords = json.dumps({'rating': effective_rating or rating, 'type': report_type}, ensure_ascii=False)
        cur.execute("""
            INSERT OR IGNORE INTO sentiment (news_id, score, keywords, method, model_version)
            VALUES (?, ?, ?, 'rating', 'v1.0')
        """, (report_id, score, keywords))
        
        cur.execute("""
            INSERT OR IGNORE INTO news_stock (news_id, ts_code, mention_type)
            VALUES (?, ?, 'direct')
        """, (report_id, ts_code))
        
        return True
    except:
        return False


# ========== 采集核心 ==========

def collect_all_notices(ts_code, conn):
    """分页拉取全部公告"""
    westock_code = ts_code_to_westock(ts_code)
    page = 0
    total_count = 0
    
    while not shutdown_flag:
        offset = page * PAGE_SIZE
        cmd = f"npx -y westock-data-skillhub@1.0.5 notice list {westock_code} --limit {PAGE_SIZE} --offset {offset}"
        output = call_westock(cmd)
        
        if not output:
            break
        
        # 第一页：提取总数
        if page == 0:
            total, total_pages = get_total_pages(output)
            if total > 0:
                print(f"[NOTICE] {ts_code} 共 {total} 条公告 ({total_pages} 页)", flush=True)
        
        items = parse_markdown_table(output)
        if not items:
            break
        
        # 批量入库（每页一次commit）
        page_count = 0
        for item in items:
            if insert_notice(item, ts_code, conn):
                page_count += 1
        conn.commit()
        total_count += page_count
        
        page += 1
        if page > 0:
            time.sleep(DELAY_BETWEEN_PAGES)
        
        # 如果返回的条数少于PAGE_SIZE，说明最后一页
        if len(items) < PAGE_SIZE:
            break
    
    return total_count


def collect_all_reports(ts_code, conn):
    """分页拉取全部研报"""
    westock_code = ts_code_to_westock(ts_code)
    page = 0
    total_count = 0
    
    while not shutdown_flag:
        offset = page * PAGE_SIZE
        cmd = f"npx -y westock-data-skillhub@1.0.5 report list {westock_code} --limit {PAGE_SIZE} --offset {offset}"
        output = call_westock(cmd)
        
        if not output:
            break
        
        if page == 0:
            total, total_pages = get_total_pages(output)
            if total > 0:
                print(f"[REPORT] {ts_code} 共 {total} 条研报 ({total_pages} 页)", flush=True)
        
        items = parse_markdown_table(output)
        if not items:
            break
        
        page_count = 0
        for item in items:
            if insert_report(item, ts_code, conn):
                page_count += 1
        conn.commit()
        total_count += page_count
        
        page += 1
        time.sleep(DELAY_BETWEEN_PAGES)
        
        if len(items) < PAGE_SIZE:
            break
    
    return total_count


def collect_stock(ts_code, conn, do_notice=True, do_report=True):
    """采集单只股票的全部公告+研报"""
    notice_count = 0
    report_count = 0
    
    if do_notice:
        notice_count = collect_all_notices(ts_code, conn)
    
    if do_report and not shutdown_flag:
        report_count = collect_all_reports(ts_code, conn)
    
    return notice_count, report_count


# ========== 股票池管理 ==========

def get_active_pool(conn):
    """L2 活跃池：stock_list中有研报覆盖的（通过近6个月有数据判断）"""
    # 先取电子+通信+计算机+半导体相关行业的所有股票
    tech_industries = [
        '电气设备', '元器件', '半导体', '通信设备', '软件服务',
        'IT设备', '互联网', '电器仪表', '家用电器', '汽车配件'
    ]
    
    placeholders = ','.join(['?' for _ in tech_industries])
    cur = conn.cursor()
    cur.execute(f"""
        SELECT ts_code FROM stock_list 
        WHERE industry IN ({placeholders})
        AND list_date < '20250101'
        ORDER BY ts_code
    """, tech_industries)
    
    return [row[0] for row in cur.fetchall()]


def get_full_pool(conn):
    """L3 全市场池：所有股票（仅公告）"""
    cur = conn.cursor()
    cur.execute("""
        SELECT ts_code FROM stock_list 
        WHERE list_date < '20250101'
        ORDER BY ts_code
    """)
    return [row[0] for row in cur.fetchall()]


# ========== 主函数 ==========

def main():
    global shutdown_flag
    
    parser = argparse.ArgumentParser(description="舆情大规模采集 v3")
    parser.add_argument('--pool', type=str, choices=['core', 'active', 'all', 'custom'],
                        help='股票池：core/active/all/custom')
    parser.add_argument('--stock', type=str, help='单只股票代码')
    parser.add_argument('--codes', type=str, help='多只股票逗号分隔（配合--pool custom使用）')
    parser.add_argument('--resume', action='store_true', help='从断点继续')
    parser.add_argument('--notice-only', action='store_true', help='仅采集公告')
    parser.add_argument('--report-only', action='store_true', help='仅采集研报')
    parser.add_argument('--reset', action='store_true', help='清除checkpoint重新开始')
    parser.add_argument('--max-stocks', type=int, default=0, help='最大处理股票数（0=全部）')
    
    args = parser.parse_args()
    
    # 清除checkpoint
    if args.reset:
        if os.path.exists(CHECKPOINT_FILE):
            os.remove(CHECKPOINT_FILE)
        print("[INFO] Checkpoint已清除")
    
    cp = load_checkpoint()
    
    # 确定股票池
    stock_codes = []
    pool_name = 'custom'
    
    if args.stock:
        stock_codes = [args.stock]
    elif args.codes:
        stock_codes = [c.strip() for c in args.codes.split(',')]
        pool_name = 'custom'
    elif args.resume and cp.get('completed_stocks'):
        # 从checkpoint恢复
        stock_codes = CORE_POOL if cp.get('pool') == 'core' else []
        pool_name = cp.get('pool', 'core')
        completed = set(cp.get('completed_stocks', []))
        stock_codes = [s for s in stock_codes if s not in completed]
        print(f"[RESUME] 恢复采集：已完成{len(completed)}只，剩余{len(stock_codes)}只")
    else:
        conn = sqlite3.connect(DB_PATH)
        if args.pool == 'core':
            stock_codes = CORE_POOL
            pool_name = 'core'
        elif args.pool == 'active':
            stock_codes = get_active_pool(conn)
            pool_name = 'active'
            print(f"[INFO] 活跃池：{len(stock_codes)} 只股票")
        elif args.pool == 'all':
            stock_codes = get_full_pool(conn)
            pool_name = 'all'
            print(f"[INFO] 全市场池：{len(stock_codes)} 只股票")
        else:
            # 默认core池
            stock_codes = CORE_POOL
            pool_name = 'core'
        conn.close()
    
    if args.max_stocks > 0:
        stock_codes = stock_codes[:args.max_stocks]
    
    # 过滤已完成的（除非是resume）
    if not args.resume and cp.get('pool') == pool_name:
        completed = set(cp.get('completed_stocks', []))
        stock_codes = [s for s in stock_codes if s not in completed]
    
    if not stock_codes:
        print("[INFO] 没有待采集的股票")
        return
    
    print(f"[开始] 采集池: {pool_name}, 共 {len(stock_codes)} 只股票")
    print(f"[配置] 公告: {'是' if not args.report_only else '否'}, 研报: {'是' if not args.notice_only else '否'}")
    print()
    
    # 连接数据库
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    
    total_notice = 0
    total_report = 0
    completed = set(cp.get('completed_stocks', []))
    
    start_time = datetime.now()
    
    for i, code in enumerate(stock_codes):
        if shutdown_flag:
            break
        
        print(f"\n[{i+1}/{len(stock_codes)}] {code} 开始采集...", flush=True)
        
        try:
            nc, rc = collect_stock(
                code, conn,
                do_notice=not args.report_only,
                do_report=not args.notice_only
            )
            
            if nc > 0 or rc > 0:
                completed.add(code)
                total_notice += nc
                total_report += rc
                
                # 每完成一只就更新checkpoint
                cp['pool'] = pool_name
                cp['completed_stocks'] = list(completed)
                save_checkpoint(cp)
                
                elapsed = (datetime.now() - start_time).total_seconds()
                rate = (i + 1) / max(elapsed, 1) * 60
                eta_total = len(stock_codes) / max(rate, 0.01)
                eta_remaining = (len(stock_codes) - i - 1) / max(rate, 0.01)
                
                print(f"[OK] {code} 公告{nc}条 + 研报{rc}条 | "
                      f"累计: {total_notice}公告/{total_report}研报 | "
                      f"速度: {rate:.1f}只/分 | "
                      f"预估剩余: {eta_remaining:.0f}分", flush=True)
            else:
                print(f"[SKIP] {code} 无数据", flush=True)
                completed.add(code)
                cp['completed_stocks'] = list(completed)
                save_checkpoint(cp)
        
        except Exception as e:
            print(f"[ERROR] {code} 异常: {e}", flush=True)
            # 不加入completed，下次重试
        
        time.sleep(DELAY_BETWEEN_STOCKS)
    
    conn.close()
    
    elapsed_total = (datetime.now() - start_time).total_seconds()
    print(f"\n{'='*60}")
    print(f"[完成] {len(completed)}只股票, 公告{total_notice}条, 研报{total_report}条")
    print(f"[耗时] {elapsed_total/60:.1f} 分钟")
    print(f"[数据库] {DB_PATH}")
    
    if completed:
        cp['completed_stocks'] = list(completed)
        save_checkpoint(cp)


if __name__ == '__main__':
    main()
