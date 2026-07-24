#!/usr/bin/env python3
"""进阶数据下载器 v2 - 内置智能限速，gycloud通道"""
import requests, sqlite3, os, sys, time, argparse
from datetime import datetime, timedelta

TOKEN = "2b6b1b830a45468b9856e6500ce40a90"
BASE = "https://ts.gyzcloud.top/api"
DB = os.path.expanduser("~/stock-data/advanced.db")
CALL_GAP = 2.5  # 每2.5秒一次，确保不超过150次/分钟

def init_db():
    os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS cyq_perf (
        ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,
        his_low REAL, his_high REAL, cost_5pct REAL, cost_15pct REAL,
        cost_50pct REAL, cost_85pct REAL, cost_95pct REAL,
        weight_avg REAL, winner_rate REAL,
        PRIMARY KEY (ts_code, trade_date));
    CREATE INDEX IF NOT EXISTS idx_cyq_d ON cyq_perf(trade_date);
    CREATE TABLE IF NOT EXISTS sw_daily (
        ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,
        open REAL, high REAL, low REAL, close REAL, vol REAL, amount REAL, pct_change REAL,
        PRIMARY KEY (ts_code, trade_date));
    CREATE INDEX IF NOT EXISTS idx_sw_d ON sw_daily(trade_date);
    CREATE TABLE IF NOT EXISTS ths_index (ts_code TEXT PRIMARY KEY, name TEXT, type TEXT, list_date TEXT);
    CREATE TABLE IF NOT EXISTS ths_member (
        ts_code TEXT NOT NULL, con_code TEXT NOT NULL, name TEXT,
        in_date TEXT, out_date TEXT, is_new TEXT,
        PRIMARY KEY (ts_code, con_code));
    """)
    return conn

def call(api, params, fields=""):
    r = requests.post(f"{BASE}/{api}", json={"api_name":api,"token":TOKEN,"params":params,"fields":fields}, timeout=15)
    d = r.json()
    if d.get("code") == 0:
        return d.get("data",{}).get("items",[])
    elif d.get("code") == -2001:
        wait = 65
        print(f"  ⏳ 限速, 等{wait}s", flush=True)
        time.sleep(wait)
        return call(api, params, fields)
    return None

def format_duration(s):
    if s < 60: return f"{s:.0f}s"
    elif s < 3600: return f"{s/60:.1f}分钟"
    else: h,m = int(s//3600),int((s%3600)//60); return f"{h}h{m}m"

def main():
    p = argparse.ArgumentParser(description="进阶数据下载器 v2")
    p.add_argument("--start","-s",type=str); p.add_argument("--end","-e",type=str)
    p.add_argument("--update","-u",action="store_true"); p.add_argument("--today",action="store_true")
    p.add_argument("--module","-m",type=str,default="all",help="all/cyq/sw/ths")
    args = p.parse_args()

    conn = init_db(); cur = conn.cursor(); today = datetime.now().strftime("%Y%m%d")

    if args.today: start = end = today
    elif args.update:
        cur.execute("SELECT MAX(trade_date) FROM sw_daily")
        r = cur.fetchone(); last = r[0] if r and r[0] else None
        start = (datetime.strptime(last,"%Y%m%d")+timedelta(days=1)).strftime("%Y%m%d") if last else "20240101"
        end = today
    else: start = args.start or today; end = args.end or today

    mod = args.module; do_cyq = mod in ("all","cyq"); do_sw = mod in ("all","sw"); do_ths = mod in ("all","ths")

    print("="*60); print("  进阶数据下载器 v2"); print("="*60)
    print(f"  模块: {mod} | 日期: {start}~{end}\n")

    # ---- 概念板块 ----
    if do_ths:
        print("📊 概念板块列表...", end=" ", flush=True)
        items = call("ths_index", {"exchange":"A","type":"N"}, "ts_code,name")
        if items:
            cur.executemany("INSERT OR REPLACE INTO ths_index VALUES(?,?,?,?)",[(str(i[0]),str(i[1]),"","") for i in items])
            conn.commit()
            codes = [i[0] for i in items]
            print(f"{len(codes)}个")
            time.sleep(CALL_GAP)

            print(f"📊 成分股 ({len(codes)}个板块)...")
            mem_total = 0; t0 = time.time()
            for idx, code in enumerate(codes):
                members = call("ths_member", {"ts_code":code}, "ts_code,con_code,name")
                if members:
                    cur.executemany("INSERT OR REPLACE INTO ths_member VALUES(?,?,?,?,?,?)",
                                   [(str(m[0]),str(m[1]),str(m[2]) if len(m)>2 else "","","","") for m in members])
                    mem_total += len(members)
                if (idx+1)%20==0:
                    conn.commit()
                    elapsed = time.time()-t0
                    eta = format_duration(elapsed/(idx+1)*(len(codes)-idx-1))
                    print(f"  {idx+1}/{len(codes)} | {mem_total:,}成分股 | 剩余~{eta}")
                time.sleep(CALL_GAP*0.5)
            conn.commit()
            print(f"  ✅ 完成: {mem_total:,} 成分股\n")

    # ---- 逐日下载筹码 & 行业 ----
    if do_cyq or do_sw:
        dates = sorted([d[0] for d in call("trade_cal",{"exchange":"SSE","start_date":start,"end_date":end,"is_open":1},"cal_date") or []])
        total = len(dates)
        if total == 0: print("无交易日"); conn.close(); return
        print(f"📅 {total}天, 预计~{format_duration(total*CALL_GAP*(1+do_cyq+do_sw))}\n")

        ct=0; st=0; t0=time.time()
        for i, dt in enumerate(dates):
            cn=0; sn=0
            if do_cyq:
                items = call("cyq_perf",{"trade_date":dt},"ts_code,trade_date,his_low,his_high,cost_5pct,cost_15pct,cost_50pct,cost_85pct,cost_95pct,weight_avg,winner_rate")
                if items:
                    cur.executemany("""INSERT OR REPLACE INTO cyq_perf VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        [(str(r[0]),str(r[1]),float(r[2]or 0),float(r[3]or 0),float(r[4]or 0),float(r[5]or 0),
                          float(r[6]or 0),float(r[7]or 0),float(r[8]or 0),float(r[9]or 0),float(r[10]or 0)) for r in items])
                    cn=len(items); ct+=cn
                time.sleep(CALL_GAP)
            if do_sw:
                items = call("sw_daily",{"trade_date":dt},"ts_code,trade_date,open,high,low,close,vol,amount,pct_change")
                if items:
                    cur.executemany("""INSERT OR REPLACE INTO sw_daily VALUES(?,?,?,?,?,?,?,?,?)""",
                        [(str(r[0]),str(r[1]),float(r[2]or 0),float(r[3]or 0),float(r[4]or 0),float(r[5]or 0),
                          float(r[6]or 0),float(r[7]or 0),float(r[8]or 0)) for r in items])
                    sn=len(items); st+=sn
                time.sleep(CALL_GAP)
            if (i+1)%5==0: conn.commit()
            elapsed = time.time()-t0
            pct = (i+1)/total*100; eta = format_duration(elapsed/(i+1)*(total-i-1)) if i>0 else "..."
            print(f"\r  [{i+1:>4}/{total}] {pct:.0f}% | {dt} | 筹码+{cn} 行业+{sn} | {ct+st:,}累计 | 剩~{eta}   ", end="", flush=True)
        conn.commit()
        tt = time.time()-t0
        print(f"\n\n{'='*60}\n  ✅ 筹码{ct:,} + 行业{st:,} | {format_duration(tt)}\n{'='*60}")

    for tbl,lab in [("cyq_perf","筹码"),("sw_daily","行业"),("ths_index","板块"),("ths_member","成分")]:
        cur.execute(f"SELECT COUNT(*) FROM {tbl}")
        cnt = cur.fetchone()[0]
        if cnt: print(f"  {lab}: {cnt:,}")
    print()
    conn.close()

if __name__ == "__main__":
    main()
