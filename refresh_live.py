#!/usr/bin/env python3
"""从新浪拉取实时行情（每小时跑一次，保持文件更新）"""
import json, subprocess, os
from datetime import datetime

LIVE = '/Users/ziruzhu/stock-data/live_prices.json'
PORTFOLIO = '/Users/ziruzhu/stock-data/portfolio.json'

# 收集所有持仓代码
codes = set()
try:
    pf = json.load(open(PORTFOLIO))
    codes = set(t['code'] for t in pf['trades'])
except:
    pass

if not codes:
    json.dump({'_time': datetime.now().strftime('%H:%M:%S')}, open(LIVE, 'w'), ensure_ascii=False)
    exit(0)

# 转为新浪格式
sina = []
mapping = {}
for c in codes:
    parts = c.split('.')
    pre = 'sh' if parts[1].lower() == 'sh' else 'sz'
    sc = f'{pre}{parts[0]}'
    sina.append(sc)
    mapping[sc] = c

# 拉取
symbols = ','.join(sina)
proc = subprocess.Popen(
    ['curl', '-s', '--max-time', '5', f'http://hq.sinajs.cn/list={symbols}',
     '-H', 'Referer: https://finance.sina.com.cn'],
    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
raw, _ = proc.communicate(timeout=8)

# 解析（GBK编码）
text = raw.decode('gbk', errors='replace')
prices = {}
for line in text.strip().split('\n'):
    if '=' not in line or '"' not in line:
        continue
    key = line.split('=')[0].replace('var hq_str_', '')
    val = line.split('"')[1] if '"' in line else ''
    parts = val.split(',')
    if len(parts) < 4 or not parts[3] or key not in mapping:
        continue
    try:
        px = float(parts[3])
        prev = float(parts[2]) if parts[2] and parts[2] != '0' else px
        prices[mapping[key]] = {
            'price': px,
            'name': parts[0],
            'pct': round((px / prev - 1) * 100, 2),
        }
    except:
        pass

prices['_time'] = datetime.now().strftime('%H:%M:%S')
json.dump(prices, open(LIVE, 'w'), ensure_ascii=False)
print(f"[{prices['_time']}] {len(prices)-1} stocks updated")
