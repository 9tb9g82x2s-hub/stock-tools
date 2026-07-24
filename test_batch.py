import requests,json
r=requests.post('https://ts.gyzcloud.top/api/daily',json={
    'api_name':'daily','token':'2b6b1b830a45468b9856e6500ce40a90',
    'params':{'trade_date':'20160104'},
    'fields':'ts_code,trade_date,close'},timeout=10)
d=r.json()
items=d.get('data',{}).get('items',[])
print(f'状态:{r.status_code} 股票数:{len(items)}')
if items:
    print(f'示例:{items[0]}, {items[1]}')
