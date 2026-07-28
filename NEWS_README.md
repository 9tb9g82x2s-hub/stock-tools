# 舆情资料库使用手册

> 建立日期：2026-07-28  
> 目标：打通「消息面 → 股价」影响链路

---

## 一、架构概览

```
数据源层：westockdata 公告 + 研报（含投资评级）
    ↓
采集层：news_collector.py 采集脚本
    ↓
存储层：stock_all.db（新增3张表 + 1个视图）
    ↓
分析层：sentiment 情绪量化 + news_stock 时间窗口关联
    ↓
应用层：可视化报告 + 策略回测验证
```

---

## 二、数据库表结构

### 2.1 news 主表（新闻/公告/研报）
| 字段 | 说明 |
|------|------|
| news_id | 唯一标识（格式：westock_notice_xxx / westock_report_xxx） |
| pub_time | 发布时间 |
| title | 标题 |
| content | 正文（可选） |
| source | 来源：westock |
| type | 类型：announcement（公告）/ report（研报） |
| raw_data | 原始JSON数据 |

### 2.2 sentiment 情绪表
| 字段 | 说明 |
|------|------|
| news_id | 关联news表 |
| score | 情绪分数：-1（负面）~ 0（中性）~ 1（正面） |
| keywords | 关键词JSON（研报含评级和类型） |
| method | 分析方法：rule（关键词规则）/ rating（投资评级） |

**情绪分数规则：**
- 研报：根据投资评级自动打分
  - 买入/强烈推荐 → 0.8 ~ 0.9
  - 增持/推荐 → 0.5 ~ 0.6
  - 中性/持有 → 0.0
  - 减持 → -0.5
  - 卖出 → -0.8
- 公告：关键词规则打分
  - 正面词：业绩、中标、合作、增长、盈利、分红等
  - 负面词：亏损、处罚、诉讼、风险、违规等

### 2.3 news_stock 关联表
| 字段 | 说明 |
|------|------|
| news_id | 关联news表 |
| ts_code | 股票代码 |
| relevance | 相关度（默认1.0） |
| mention_type | 提及类型：direct（直接）/ industry（行业）/ related（相关） |

### 2.4 v_stock_news_summary 视图
按股票汇总舆情统计：
- 公告数 / 研报数
- 平均情绪分数
- 最早/最新消息时间

---

## 三、采集脚本使用

### 3.1 基础用法

```bash
# 采集单只股票
python3 news_collector.py --stock 300750.SZ --limit 10

# 采集多只股票
python3 news_collector.py --codes 300750.SZ,600519.SH,000858.SZ --limit 20

# 使用预设股票池
python3 news_collector.py --pool tech      # 科技股池（10只）
python3 news_collector.py --pool my-watch  # 自选股池

# 只采集公告或研报
python3 news_collector.py --stock 300750.SZ --notice-only
python3 news_collector.py --stock 300750.SZ --report-only
```

### 3.2 预设股票池

脚本内置两个股票池，可在 `news_collector.py` 中自定义：

- **tech**：科技股TOP10（电子元件、软件、通信设备）
- **my-watch**：你的自选股（默认示例，可改）

### 3.3 定时采集（推荐）

每日早上6点自动采集前一日数据：

**macOS launchd 方式：**
```bash
# 创建 ~/Library/LaunchAgents/com.stock.news.plist
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.stock.news</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/ziruzhu/stock-tools/news_collector.py</string>
        <string>--pool</string>
        <string>tech</string>
        <string>--limit</string>
        <string>30</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>6</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/Users/ziruzhu/stock-tools/logs/news_collector.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/ziruzhu/stock-tools/logs/news_collector_error.log</string>
</dict>
</plist>

# 加载定时任务
launchctl load ~/Library/LaunchAgents/com.stock.news.plist
```

---

## 四、数据查询示例

### 4.1 查看入库统计
```sql
-- 总数统计
SELECT type, COUNT(*) as cnt FROM news GROUP BY type;

-- 按股票汇总
SELECT * FROM v_stock_news_summary ORDER BY news_count DESC LIMIT 20;
```

### 4.2 查看某只股票的舆情
```sql
-- 查看宁德时代最近的公告和研报
SELECT 
    n.pub_time,
    n.type,
    n.title,
    s.score as sentiment
FROM news n
JOIN news_stock ns ON n.news_id = ns.news_id
LEFT JOIN sentiment s ON n.news_id = s.news_id
WHERE ns.ts_code = '300750.SZ'
ORDER BY n.pub_time DESC
LIMIT 20;
```

### 4.3 查看高情绪分数的消息
```sql
-- 最近的利好消息（情绪>0.5）
SELECT 
    ns.ts_code,
    sl.name,
    n.pub_time,
    n.type,
    n.title,
    s.score
FROM sentiment s
JOIN news n ON s.news_id = n.news_id
JOIN news_stock ns ON n.news_id = ns.news_id
JOIN stock_list sl ON ns.ts_code = sl.ts_code
WHERE s.score > 0.5
  AND n.pub_time >= '2026-07-01'
ORDER BY s.score DESC, n.pub_time DESC
LIMIT 30;
```

### 4.4 统计研报评级分布
```sql
-- 各股票的买入评级研报数量
SELECT 
    ns.ts_code,
    sl.name,
    COUNT(*) as buy_rating_count,
    AVG(s.score) as avg_sentiment
FROM sentiment s
JOIN news n ON s.news_id = n.news_id
JOIN news_stock ns ON n.news_id = ns.news_id
JOIN stock_list sl ON ns.ts_code = sl.ts_code
WHERE n.type = 'report'
  AND s.score >= 0.8
GROUP BY ns.ts_code
ORDER BY buy_rating_count DESC
LIMIT 20;
```

---

## 五、下一步开发

### 阶段2：消息面-股价关联分析
创建 `sentiment_impact.py`：

**功能：**
1. 计算消息发布后 T+1/T+3/T+5 的股价变化
2. 统计不同情绪分数区间的平均涨跌幅
3. 生成 HTML 可视化报告

**关键逻辑：**
```python
def calc_price_impact(news_id, ts_code, window=[1,3,5]):
    # 获取消息发布时间
    pub_time = get_news_time(news_id)
    
    # 找到发布前最后一个交易日的收盘价
    base_close = get_close_before(ts_code, pub_time)
    
    # 计算T+N涨跌幅
    for n in window:
        future_close = get_close_after(ts_code, pub_time, n)
        impact = (future_close - base_close) / base_close
```

### 阶段3：策略整合
在现有回测框架中加入消息面过滤：

```python
# OBV+MACD擒牛策略 + 消息面过滤
def filter_by_sentiment(ts_code, trade_date):
    # 查询前3日是否有负面消息
    recent_sentiment = get_recent_sentiment(ts_code, trade_date, days=3)
    
    # 有强负面（<-0.5）→ 跳过
    if recent_sentiment < -0.5:
        return False
    
    # 有强利好（>0.7）→ 加码
    if recent_sentiment > 0.7:
        return 'strong_buy'
    
    return True
```

---

## 六、注意事项

1. **数据源限制**
   - westockdata 公告数据完整，但研报可能不含全文，只有标题和评级
   - 历史数据回溯深度：公告可追溯多年，研报一般1-2年

2. **情绪分数局限性**
   - 规则打分简单粗暴，误判率较高（如"亏损转盈利"会被识别为负面）
   - 投资评级打分较准，但券商有时会虚高评级

3. **增量采集建议**
   - 每日采集时 limit 设为 30，确保覆盖当日全部公告
   - 如果股票池很大（>50只），分批采集避免超时

4. **扩展方向**
   - 接入 Ollama 本地模型做深度情绪分析（替代规则打分）
   - 爬取东方财富股吧/雪球散户情绪（需爬虫）
   - 整合进你的投资组合管理系统（portfolio-api skill）

---

## 七、快速诊断

```bash
# 检查表是否存在
sqlite3 ~/stock-data/stock_all.db ".tables" | grep news

# 检查入库数量
sqlite3 ~/stock-data/stock_all.db "SELECT COUNT(*) FROM news;"

# 查看最新5条数据
sqlite3 ~/stock-data/stock_all.db "SELECT pub_time, type, title FROM news ORDER BY pub_time DESC LIMIT 5;"

# 检查情绪分析覆盖率
sqlite3 ~/stock-data/stock_all.db "
SELECT 
    (SELECT COUNT(*) FROM sentiment) as sentiment_count,
    (SELECT COUNT(*) FROM news) as news_count,
    ROUND(CAST((SELECT COUNT(*) FROM sentiment) AS FLOAT) / (SELECT COUNT(*) FROM news) * 100, 2) as coverage_pct;
"
```

---

**最后更新：** 2026-07-28  
**维护者：** 泓锦  
**数据库位置：** ~/stock-data/stock_all.db
