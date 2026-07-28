"""探测gycloud接口(正确/api路径):概念成分ths_member + 概念日线ths_daily"""
import requests, json

TOKEN = "2b6b1b830a45468b9856e6500ce40a90"
BASE = "https://ts.gyzcloud.top/api"

def call(api_name, params, fields=""):
    body = {"api_name": api_name, "token": TOKEN, "params": params, "fields": fields}
    try:
        r = requests.post(BASE, json=body, timeout=30)
        try:
            j = r.json()
        except Exception:
            print(f"  [{api_name}] 非JSON, status={r.status_code}, 前200字:{r.text[:200]}")
            return None
        if j.get("code") == 0:
            return j["data"]
        print(f"  [{api_name}] code={j.get('code')} msg={j.get('msg')}")
        return None
    except Exception as e:
        print(f"  [{api_name}] 异常: {e}")
        return None

print("=== 探 ths_member (概念成分, 消费电子881124.TI) ===")
d = call("ths_member", {"ts_code": "881124.TI"}, "ts_code,con_code,con_name,weight")
if d:
    print(f"  成功! 字段:{d['fields']}, 成分数:{len(d['items'])}")
    print(f"  前5:{d['items'][:5]}")

print("\n=== 探 ths_daily (概念日线 881124.TI) ===")
d = call("ths_daily", {"ts_code": "881124.TI", "start_date": "20260601", "end_date": "20260630"}, "ts_code,trade_date,close,pct_change")
if d:
    print(f"  成功! 字段:{d['fields']}, 行数:{len(d['items'])}")
    print(f"  前3:{d['items'][:3]}")

print("\n=== 探 ths_index (概念列表验证接口) ===")
d = call("ths_index", {"exchange": "A", "type": "N"}, "ts_code,name,count")
if d:
    print(f"  成功! 概念数:{len(d['items'])}")
