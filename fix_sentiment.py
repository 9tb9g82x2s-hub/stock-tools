#!/usr/bin/env python3
"""修复空评级研报的情绪分数，用标题关键词重新打分"""

import sqlite3
import json
import re

DB_PATH = "/Users/ziruzhu/stock-data/stock_all.db"

def fix_sentiment():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    # 找出所有评级为空、score=0的研报
    cur.execute("""
        SELECT s.id, s.news_id, n.title, s.keywords
        FROM sentiment s
        JOIN news n ON s.news_id = n.news_id
        WHERE s.method = 'rating' 
        AND (s.score = 0.0 OR s.score IS NULL)
        AND (s.keywords LIKE '%"rating": ""%' OR s.keywords IS NULL)
    """)
    
    rows = cur.fetchall()
    print(f"待修复: {len(rows)} 条")
    
    pos_kw = ['买入', '增持', '强推', '推荐', '买进', '看好', '超预期', '增长', '业绩']
    neg_kw = ['减持', '卖出', '中性', '风险', '下滑', '低于预期', '亏损']
    
    fixed = 0
    for sid, news_id, title, keywords in rows:
        # 标题中关键词打分
        text = (title or '').lower()
        pos = sum(1 for kw in pos_kw if kw in text)
        neg = sum(1 for kw in neg_kw if kw in text)
        
        if pos + neg > 0:
            score = round((pos - neg) / (pos + neg), 2)
        else:
            score = 0.0
        
        # 更新评分
        new_kw = json.dumps({'rating': 'inferred', 'method': 'title_keywords'}, ensure_ascii=False)
        cur.execute("""
            UPDATE sentiment 
            SET score = ?, keywords = ?, method = 'rating_fixed', model_version = 'v1.1'
            WHERE id = ?
        """, (score, new_kw, sid))
        
        fixed += 1
    
    conn.commit()
    conn.close()
    print(f"已修复: {fixed} 条")
    
    # 验证
    conn = sqlite3.connect(DB_PATH)
    new_dist = conn.execute("""
        SELECT 
            CASE 
                WHEN s.score >= 0.8 THEN '买入/强推'
                WHEN s.score >= 0.5 THEN '增持/推荐'
                WHEN s.score > 0 THEN '偏正面'
                WHEN s.score = 0 THEN '中性'
                WHEN s.score > -0.5 THEN '偏负面'
                ELSE '负面'
            END as rating,
            COUNT(*) as cnt
        FROM sentiment s
        JOIN news n ON s.news_id = n.news_id
        WHERE n.type = 'report'
        GROUP BY 1 ORDER BY s.score DESC
    """).fetchall()
    
    print("\n修复后研报情绪分布:")
    for rating, cnt in new_dist:
        print(f"  {rating}: {cnt}")

if __name__ == '__main__':
    fix_sentiment()
