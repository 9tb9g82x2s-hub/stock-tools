"""探测gycloud接口:概念成分ths_member + 概念日线ths_daily 能否拉取"""
import requests, json

TOKEN="2b6b1b830a45468b9856e6500ce40a90"
URL="http://ts.gyzcloud.top"  # 先试这个host

def call(api_name, params, fields=""):
    payload={"api_name":api_name,"token":TOKEN,"params":params,"fields":fields}
    for host in ["http://ts.gyzcloud.top","https://ts.gyzcloud.top","http://api.tushare.pro"]:
        try:
            r=requests.post(host,json=payload,timeout=15)
            j=r.json()
            if j.get("code")==0:
                return host, j["data"]
            else:
                print(f"  [{host}] {api_name} 返回: code={j.get('code')} msg={j.get('msg')}")
        except Exception as e:
            print(f"  [{host}] {api_name} 异常: {e}")
    return None, None

# 1. 探概念成分 ths_member (选一个概念:消费电子 881124.TI)
print("=== 探 ths_member (概念成分) ===")
host, data = call("ths_member", {"ts_code":"881124.TI"}, "ts_code,con_code,con_name")
if data:
    print(f"  成功! host={host}, 字段:{data['fields']}, 成分数:{len(data['items'])}")
    print(f"  前5个成分:{data['items'][:5]}")
else:
    print("  ths_member 拉取失败")

# 2. 探概念日线 ths_daily
print("\n=== 探 ths_daily (概念日线) ===")
host, data = call("ths_daily", {"ts_code":"881124.TI","start_date":"20260601","end_date":"20260630"}, "ts_code,trade_date,close,pct_change")
if data:
    print(f"  成功! host={host}, 字段:{data['fields']}, 行数:{len(data['items'])}")
    print(f"  前3行:{data['items'][:3]}")
else:
    print("  ths_daily 拉取失败")

# 3. 探 ths_index (概念列表,验证接口通)
print("\n=== 探 ths_index (概念列表) ===")
host, data = call("ths_index", {"exchange":"A","type":"N"}, "ts_code,name,count")
if data:
    print(f"  成功! host={host}, 概念数:{len(data['items'])}")
