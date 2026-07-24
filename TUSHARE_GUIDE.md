# Tushare Pro 数据平台 · 使用指南

> 整理自 Tushare 官方文档 | 最后更新: 2026-07-12

---

## 一、快速入门

### 注册与Token

1. 访问 https://tushare.pro 注册账号
2. 个人主页获取API Token
3. 修改个人信息可得20积分（注册100+修改20=120积分，够高频使用）

### 两种调用方式

**方式一：Python SDK**
```python
import tushare as ts
ts.set_token('your_token')
pro = ts.pro_api()
df = pro.daily(trade_date='20200325')
```

**方式二：HTTP RESTful**
```bash
curl -X POST -d '{"api_name":"trade_cal","token":"xxx","params":{...},"fields":"..."}' http://api.tushare.pro
```

---

## 二、高效撸数据（核心经验）

### 原则：按交易日循环，不按股票代码循环

```
❌ 错误: 5000+只股票 × 循环 = 极慢
✅ 正确: ~220个交易日/年 × 循环 = 高效
```

```python
pro = ts.pro_api()

# 1. 拿交易日历
df = pro.trade_cal(exchange='SSE', is_open='1', start_date='20200101', end_date='20200401')

# 2. 逐天拉全市场
for date in df['cal_date'].values:
    df = pro.daily(trade_date=date)
```

### 重试机制

```python
def get_daily(pro, trade_date='', ts_code='', start_date='', end_date=''):
    for _ in range(3):
        try:
            if trade_date:
                return pro.daily(ts_code=ts_code, trade_date=trade_date)
            else:
                return pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        except:
            time.sleep(1)
    return None
```

---

## 三、数据入库

### MySQL（sqlalchemy）

```python
from sqlalchemy import create_engine
engine = create_engine('mysql+pymysql://root:pwd@localhost:3306/stock_db')
df.to_sql('stock_basic', engine, index=False, if_exists='append', chunksize=5000)
```

**优化建议：**
- 预建表，用VARCHAR代替TEXT
- 加PRIMARY KEY和索引
- 加COMMENT注释

### MongoDB（pymongo）

```python
from pymongo import MongoClient
client = MongoClient(host='localhost', port=27017, username='root', password='mima123',
                     authSource='admin', authMechanism='SCRAM-SHA-1')

def insert_mongo(df):
    db = client['demos']
    collection = db['stock_basic']
    collection.insert_many(df.to_dict('records'))
```

---

## 四、分钟数据

### 关键限制

- 需单独开权限（联系微信 waditu_a 或QQ群）
- 单次最多8000行
- 按标的逐个获取
- 时间格式需带时分秒：`2020-01-07 09:00:00`
- 每天17~21点更新
- 频度：1min / 5min / 15min / 30min / 60min

### 调用方式

```python
# 股票1分钟
df = ts.pro_bar(ts_code='600000.SH', freq='1min',
                start_date='2020-01-07 09:00:00', end_date='2020-01-08 17:00:00')

# 指数 (asset='I')
df = ts.pro_bar(ts_code='000001.SH', asset='I', freq='1min', ...)

# 基金 (asset='FD')
df = ts.pro_bar(ts_code='150018.SZ', asset='FD', freq='1min', ...)

# 期货 (asset='FT')
df = ts.pro_bar(ts_code='CU2012.SHF', asset='FT', freq='1min', ...)
```

---

## 五、AI生态融合

### 三层架构

| 层次 | 能力 | 适用场景 |
|------|------|---------|
| SDK层 | Python SDK + HTTP RESTful | AI Coding、脚本编程 |
| Skills层 | tushare-data Skill | AI自主理解意图、匹配接口 |
| MCP层 | Model Context Protocol | 零代码自然语言取数 |

### tushare-data Skill

- GitHub: https://github.com/waditu-tushare/skills
- 220+接口结构化文档
- 支持自然语言驱动
- 安装: `npx skills add https://github.com/waditu-tushare/skills.git --skill tushare-data`

### 适配平台

Cursor、Claude Code、Cline、Trae、OpenClaw 等主流AI工具

---

## 六、常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| Token无效 | 旧token过期/未从个人主页复制最新 | 重新登录tushare.pro复制 |
| 权限不足 | 积分不够或该接口需单独开权限 | 升级积分/联系管理员 |
| IP限制 | 多IP同时在线/使用代理池 | 单IP使用，关VPN代理 |
| 海外限制 | 服务器在国内，禁止数据出境 | 关梯子，开国内代理 |
| 模型幻觉 | AI编造数据 | 安装Skills + 让AI给出执行过程 + 交叉验证 |
| 单次超8000行 | 分钟数据限制 | 自动分段拉取 |

---

## 七、数据品类总览

| 品类 | 内容 |
|------|------|
| 股票 | 行情、财务报表、公告、分红送转、十大股东、解禁 |
| 基金 | 净值、持仓、经理、规模、ETF |
| 期货 | 行情、仓单、持仓、基差 |
| 债券 | 行情、信用评级、转债 |
| 指数 | 行情、成分股、权重 |
| 宏观 | GDP、CPI、PPI、PMI、利率、汇率 |
| 另类 | 产业、政策法规、舆情 |

---

## 八、当前环境工具

| 工具 | 数据源 | 引擎 | 用途 |
|------|--------|------|------|
| `download_v5_gycloud.py` | gycloud HTTP | SQLite/MySQL/MongoDB | 日线全市场（主力） |
| `download_tushare_v5.py` | Tushare Pro SDK | SQLite/MySQL/MongoDB | 日线（需Pro官方token） |
| `download_minute.py` | Tushare Pro SDK | SQLite | 分钟数据（需分钟权限） |
| `download_fast.py` | akshare/新浪 | SQLite | 日线（免费免token） |
