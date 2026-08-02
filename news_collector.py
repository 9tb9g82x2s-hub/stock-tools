#!/usr/bin/env python3
"""
舆情数据采集脚本 v2
数据源：
  - westockdata notice list（公司公告）
  - westockdata report list（券商研报，含投资评级）

用法：
  python3 news_collector.py                    # 采集预设股票池的公告+研报
  python3 news_collector.py --stock 300750.SZ  # 单只股票
  python3 news_collector.py --codes 300750.SZ,600519.SH --limit 10
  python3 news_collector.py --pool tech        # 科技股池
  python3 news_collector.py --pool my-watch    # 你的自选股
"""

import sqlite3
import os
import sys
import json
import argparse
import subprocess
import re
from datetime import datetime, timedelta

# ========== 配置 ==========
DB_PATH = os.path.expanduser("~/stock-data/stock_all.db")

# 预设股票池
STOCK_POOLS = {
    'tech': [  # 科技股TOP20
        '300750.SZ', '002475.SZ', '300661.SZ', '002049.SZ',  # 电子元件
        '300059.SZ', '002236.SZ', '603986.SH',              # 软件
        '600522.SH', '603160.SH', '688981.SH',              # 通信设备
    ],
    'my-watch': [  # 你的自选（示例，可改）
        '300750.SZ', '600519.SH', '000858.SZ'
    ]
}

# ========== 工具函数 ==========

def ts_code_to_westock(ts_code):
    """转换代码格式：300750.SZ -> sz300750"""
    parts = ts_code.split('.')
    if len(parts) == 2:
        return parts[1].lower() + parts[0]
    return ts_code.lower()


def parse_markdown_table(text):
    """解析 markdown 表格为列表（自动跳过统计行，定位表头）"""
    lines = text.strip().split('\n')
    
    # 找到表头行：第一个以 | 开头且包含多个 | 的行
    header_idx = -1
    for i, line in enumerate(lines):
        if line.strip().startswith('|') and line.count('|') >= 3:
            # 下一行应该是分隔符行（含 ---）
            if i + 1 < len(lines) and '---' in lines[i + 1]:
                header_idx = i
                break
    
    if header_idx == -1:
        return []
    
    headers = [h.strip() for h in lines[header_idx].split('|')[1:-1]]
    data_lines = lines[header_idx + 2:]  # 跳过表头和分隔行
    
    result = []
    for line in data_lines:
        if not line.strip() or not line.strip().startswith('|'):
            continue
        cells = [c.strip() for c in line.split('|')[1:-1]]
        if len(cells) == len(headers):
            row = dict(zip(headers, cells))
            result.append(row)
    
    return result


def calc_sentiment_from_rating(rating, title=""):
    """根据研报评级计算情绪分数，评级为空时用标题关键词推断"""
    rating_lower = rating.lower().strip() if rating else ""
    rating_map = {
        '买入': 0.8, 'buy': 0.8, '强烈推荐': 0.9, '强推': 0.8,
        '增持': 0.5, 'accumulate': 0.5, '推荐': 0.6,
        '中性': 0.0, 'hold': 0.0, '持有': 0.0,
        '减持': -0.5, 'reduce': -0.5,
        '卖出': -0.8, 'sell': -0.8,
    }
    for key, score in rating_map.items():
        if key and key in rating_lower:
            return score
    if title:
        text = title.lower()
        pos_kw = ['买入', '增持', '强推', '推荐', '买进', '看好', '超预期', '增长']
        neg_kw = ['减持', '卖出', '中性', '风险', '下滑', '低于预期', '亏损', '谨慎']
        pos = sum(1 for kw in pos_kw if kw in text)
        neg = sum(1 for kw in neg_kw if kw in text)
        if pos + neg > 0:
            return round((pos - neg) / (pos + neg), 2)
    return 0.0


def calc_sentiment_rule(title):
    """关键词规则打分（公告用）"""
    text = title.lower()
    
    positive_kw = [
        '业绩', '中标', '合作', '增长', '盈利', '分红',
        '授权', '获批', '扩产', '订单', '升级'
    ]
    negative_kw = [
        '亏损', '处罚', '诉讼', '风险', '下滑', '违规',
        '调查', '停牌', '债务', '减持', '质押'
    ]
    
    pos = sum(1 for kw in positive_kw if kw in text)
    neg = sum(1 for kw in negative_kw if kw in text)
    
    total = pos + neg
    if total == 0:
        return 0.0
    return (pos - neg) / total


# ========== 数据采集 ==========

def collect_notices(ts_code, limit, conn):
    """采集公告"""
    westock_code = ts_code_to_westock(ts_code)
    print(f"[INFO] 采集 {ts_code} 公告，最近{limit}条")
    
    cmd = f"npx -y westock-data-skillhub@1.0.5 notice list {westock_code} --limit {limit}"
    
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            print(f"[ERROR] {ts_code}公告采集失败: {result.stderr}")
            return 0
        
        output = result.stdout
        if '暂无数据' in output or '未找到' in output:
            print(f"[WARN] {ts_code} 无公告数据")
            return 0
        
        # 解析markdown表格
        notices = parse_markdown_table(output)
        if not notices:
            print(f"[WARN] {ts_code} 公告解析失败")
            return 0
        
        cur = conn.cursor()
        count = 0
        
        for item in notices:
            notice_id = f"westock_notice_{item.get('id', '')}"
            pub_time = item.get('time', '')
            title = item.get('title', '')
            
            # 插入news表
            try:
                cur.execute("""
                    INSERT OR IGNORE INTO news 
                    (news_id, pub_time, title, source, type, raw_data)
                    VALUES (?, ?, ?, 'westock', 'announcement', ?)
                """, (notice_id, pub_time, title, json.dumps(item, ensure_ascii=False)))
                
                # 情绪分析
                score = calc_sentiment_rule(title)
                cur.execute("""
                    INSERT OR IGNORE INTO sentiment 
                    (news_id, score, method, model_version)
                    VALUES (?, ?, 'rule', 'v1.0')
                """, (notice_id, score))
                
                # 关联股票
                cur.execute("""
                    INSERT OR IGNORE INTO news_stock 
                    (news_id, ts_code, mention_type)
                    VALUES (?, ?, 'direct')
                """, (notice_id, ts_code))
                
                count += 1
            except Exception as e:
                print(f"[WARN] 公告入库失败: {e}")
        
        conn.commit()
        print(f"  ✓ {ts_code} 公告入库: {count}条")
        return count
        
    except subprocess.TimeoutExpired:
        print(f"[ERROR] {ts_code} 公告采集超时")
        return 0
    except Exception as e:
        print(f"[ERROR] {ts_code} 公告采集异常: {e}")
        return 0


def collect_reports(ts_code, limit, conn):
    """采集研报"""
    westock_code = ts_code_to_westock(ts_code)
    print(f"[INFO] 采集 {ts_code} 研报，最近{limit}条")
    
    cmd = f"npx -y westock-data-skillhub@1.0.5 report list {westock_code} --limit {limit}"
    
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            print(f"[ERROR] {ts_code}研报采集失败: {result.stderr}")
            return 0
        
        output = result.stdout
        if '暂无数据' in output or '未找到' in output:
            print(f"[WARN] {ts_code} 无研报数据")
            return 0
        
        reports = parse_markdown_table(output)
        if not reports:
            print(f"[WARN] {ts_code} 研报解析失败")
            return 0
        
        cur = conn.cursor()
        count = 0
        
        for item in reports:
            report_id = f"westock_report_{item.get('id', '')}"
            pub_time = item.get('time', '')
            title = item.get('title', '')
            rating = item.get('tzpj', '')  # 投资评级
            report_type = item.get('typeStr', '')
            
            # 插入news表
            try:
                cur.execute("""
                    INSERT OR IGNORE INTO news 
                    (news_id, pub_time, title, source, type, raw_data)
                    VALUES (?, ?, ?, 'westock', 'report', ?)
                """, (report_id, pub_time, title, json.dumps(item, ensure_ascii=False)))
                
                # 情绪分析：使用投资评级
                score = calc_sentiment_from_rating(rating)
                keywords = json.dumps({'rating': rating, 'type': report_type}, ensure_ascii=False)
                cur.execute("""
                    INSERT OR IGNORE INTO sentiment 
                    (news_id, score, keywords, method, model_version)
                    VALUES (?, ?, ?, 'rating', 'v1.0')
                """, (report_id, score, keywords))
                
                # 关联股票
                cur.execute("""
                    INSERT OR IGNORE INTO news_stock 
                    (news_id, ts_code, mention_type)
                    VALUES (?, ?, 'direct')
                """, (report_id, ts_code))
                
                count += 1
            except Exception as e:
                print(f"[WARN] 研报入库失败: {e}")
        
        conn.commit()
        print(f"  ✓ {ts_code} 研报入库: {count}条")
        return count
        
    except subprocess.TimeoutExpired:
        print(f"[ERROR] {ts_code} 研报采集超时")
        return 0
    except Exception as e:
        print(f"[ERROR] {ts_code} 研报采集异常: {e}")
        return 0


# ========== 主函数 ==========

def main():
    parser = argparse.ArgumentParser(description="舆情数据采集")
    parser.add_argument('--stock', type=str, help='单只股票代码（如300750.SZ）')
    parser.add_argument('--codes', type=str, help='多只股票，逗号分隔')
    parser.add_argument('--pool', type=str, choices=list(STOCK_POOLS.keys()), 
                        help='使用预设股票池：tech/my-watch')
    parser.add_argument('--limit', type=int, default=20, 
                        help='每只股票采集的公告+研报数量（默认20）')
    parser.add_argument('--notice-only', action='store_true', help='只采集公告')
    parser.add_argument('--report-only', action='store_true', help='只采集研报')
    
    args = parser.parse_args()
    
    # 确定股票列表
    stock_codes = []
    if args.stock:
        stock_codes = [args.stock]
    elif args.codes:
        stock_codes = [c.strip() for c in args.codes.split(',')]
    elif args.pool:
        stock_codes = STOCK_POOLS[args.pool]
    else:
        # 默认使用tech池
        stock_codes = STOCK_POOLS['tech']
    
    print(f"[开始] 采集{len(stock_codes)}只股票的舆情数据，每只{args.limit}条")
    print(f"目标：{', '.join(stock_codes[:5])}{'...' if len(stock_codes)>5 else ''}\n")
    
    # 连接数据库
    conn = sqlite3.connect(DB_PATH)
    
    total_notice = 0
    total_report = 0
    
    for code in stock_codes:
        code = code.strip()
        
        if not args.report_only:
            total_notice += collect_notices(code, args.limit, conn)
        
        if not args.notice_only:
            total_report += collect_reports(code, args.limit, conn)
        
        print()  # 空行分隔
    
    conn.close()
    
    print(f"\n{'='*60}")
    print(f"[完成] 公告{total_notice}条，研报{total_report}条")
    print(f"数据库：{DB_PATH}")
    print(f"\n下一步查看数据：")
    print(f"  sqlite3 {DB_PATH} \"SELECT COUNT(*) FROM news;\"")
    print(f"  sqlite3 {DB_PATH} \"SELECT * FROM v_stock_news_summary LIMIT 10;\"")


if __name__ == '__main__':
    main()
