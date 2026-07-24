#!/bin/bash
# 每30秒拉一次新浪实时行情，写到JSON文件
LIVE_FILE="/Users/ziruzhu/stock-data/live_prices.json"
DATA="/Users/ziruzhu/stock-data/portfolio.json"

while true; do
    # 从portfolio.json取所有持仓代码
    CODES=$(python3 -c "
import json
pf=json.load(open('$DATA'))
codes=list(set(t['code'] for t in pf['trades']))
sina=[]
for c in codes:
    mkt=c.split('.')[1].lower()
    pre='sh' if mkt=='sh' else 'sz'
    sina.append(pre+c.split('.')[0])
print(','.join(sina))
" 2>/dev/null)

    if [ -n "$CODES" ]; then
        RAW=$(curl -s --max-time 3 "http://hq.sinajs.cn/list=$CODES" -H "Referer: https://finance.sina.com.cn" 2>/dev/null)
        python3 -c "
import json
raw='''$RAW'''
prices={}
for line in raw.strip().split('\n'):
    if '=' not in line: continue
    key=line.split('=')[0].replace('var hq_str_','')
    parts=line.split('\"')[1].split(',') if '\"' in line else []
    if len(parts)>3 and parts[3]:
        try:
            code=parts[0]
            px=float(parts[3])
            pct=round((float(parts[3])/float(parts[2])-1)*100,2) if parts[2] and float(parts[2])>0 else 0
            prices[key]={'price':px,'pct':pct}
        except: pass
json.dump(prices,open('$LIVE_FILE','w'),ensure_ascii=False)
" 2>/dev/null
    fi
    sleep 30
done
