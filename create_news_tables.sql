-- 舆情资料库表结构
-- 创建日期：2026-07-28
-- 用途：关联消息面与股价

-- 1. 新闻/公告/研报主表
CREATE TABLE IF NOT EXISTS news (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    news_id TEXT UNIQUE NOT NULL,      -- 唯一标识（来源_ID）
    pub_time TEXT NOT NULL,            -- 发布时间 YYYY-MM-DD HH:MM:SS
    title TEXT NOT NULL,               -- 标题
    content TEXT,                      -- 正文
    source TEXT,                       -- 来源：tushare/westock
    type TEXT NOT NULL,                -- 类型：news/announcement/report
    url TEXT,                          -- 原文链接
    raw_data TEXT,                     -- 原始JSON数据
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_news_time ON news(pub_time);
CREATE INDEX IF NOT EXISTS idx_news_type ON news(type);

-- 2. 情绪分析表
CREATE TABLE IF NOT EXISTS sentiment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    news_id TEXT NOT NULL,
    score REAL,                        -- 情绪分数 -1(负面)到1(正面)
    keywords TEXT,                     -- 关键词JSON数组
    method TEXT,                       -- 分析方法：rule/ollama/api
    model_version TEXT,                -- 模型版本
    analyzed_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (news_id) REFERENCES news(news_id)
);

CREATE INDEX IF NOT EXISTS idx_sentiment_news ON sentiment(news_id);
CREATE INDEX IF NOT EXISTS idx_sentiment_score ON sentiment(score);

-- 3. 新闻-股票关联表
CREATE TABLE IF NOT EXISTS news_stock (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    news_id TEXT NOT NULL,
    ts_code TEXT NOT NULL,             -- 股票代码
    relevance REAL DEFAULT 1.0,        -- 相关度 0-1
    mention_type TEXT,                 -- 提及类型：direct/industry/related
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (news_id) REFERENCES news(news_id),
    FOREIGN KEY (ts_code) REFERENCES stock_list(ts_code)
);

CREATE INDEX IF NOT EXISTS idx_ns_stock ON news_stock(ts_code);
CREATE INDEX IF NOT EXISTS idx_ns_news ON news_stock(news_id);
CREATE INDEX IF NOT EXISTS idx_ns_time ON news_stock(created_at);

-- 4. 辅助视图：股票舆情汇总
CREATE VIEW IF NOT EXISTS v_stock_news_summary AS
SELECT 
    ns.ts_code,
    sb.name AS stock_name,
    n.type,
    COUNT(*) AS news_count,
    AVG(s.score) AS avg_sentiment,
    MIN(n.pub_time) AS earliest_news,
    MAX(n.pub_time) AS latest_news
FROM news_stock ns
JOIN news n ON ns.news_id = n.news_id
JOIN stock_list sb ON ns.ts_code = sb.ts_code
LEFT JOIN sentiment s ON n.news_id = s.news_id
GROUP BY ns.ts_code, n.type;
