#!/usr/bin/env python3
"""
Air → Studio 增量数据库同步 v2
1. 从Air导出增量CSV → 2. SFTP传到Studio → 3. Studio本地sqlite3导入
"""
import paramiko, sqlite3, os, tempfile, time
from datetime import datetime

AIR_DB = '/Users/ziruzhu/stock-data/stock_all.db'
STUDIO_HOST = 'ziruzhudeMac-Studio.local'
STUDIO_USER = 'ziruzhu'
STUDIO_PASS = '470825'
STUDIO_DB = '/Users/ziruzhu/stock-data/stock_all.db'

TABLES = [
    {'table':'daily', 'cols':'ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount'},
    {'table':'daily_basic', 'cols':'ts_code,trade_date,turnover_rate,volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,total_share,float_share,free_share,total_mv,circ_mv'},
    {'table':'moneyflow', 'cols':'ts_code,trade_date,buy_sm_vol,buy_sm_amount,sell_sm_vol,sell_sm_amount,buy_md_vol,buy_md_amount,sell_md_vol,sell_md_amount,buy_lg_vol,buy_lg_amount,sell_lg_vol,sell_lg_amount,buy_elg_vol,buy_elg_amount,sell_elg_vol,sell_elg_amount,net_mf_vol,net_mf_amount'},
    {'table':'stk_factor', 'cols':'ts_code,trade_date,close,open,high,low,pre_close,change,pct_change,vol,amount,adj_factor,open_hfq,open_qfq,close_hfq,close_qfq,high_hfq,high_qfq,low_hfq,low_qfq,pre_close_hfq,pre_close_qfq,macd_dif,macd_dea,macd,kdj_k,kdj_d,kdj_j,rsi_6,rsi_12,rsi_24,boll_upper,boll_mid,boll_lower,cci'},
    {'table':'ggt_daily', 'cols':'trade_date,buy_amount,buy_volume,sell_amount,sell_volume'},
    {'table':'top_list', 'cols':'trade_date,ts_code,name,close,pct_change,turnover_rate,amount,l_sell,l_buy,l_amount,net_amount,net_rate,amount_rate,float_values,reason'},
    {'table':'margin', 'cols':'trade_date,exchange_id,rzye,rzmre,rzche,rqye,rqmcl,rzrqye,rqyl'},
    {'table':'margin_detail', 'cols':'ts_code,trade_date,rzye,rqye,rzmre,rqyl,rzche,rqchl'},
]

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

log("===== Air → Studio 增量同步 v2 =====")

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(STUDIO_HOST, username=STUDIO_USER, password=STUDIO_PASS, timeout=15)
sftp = ssh.open_sftp()

air = sqlite3.connect(AIR_DB)

tmpdir = tempfile.mkdtemp(prefix='stock_sync_')
log(f"临时目录: {tmpdir}")

total = 0
for t in TABLES:
    table = t['table']
    cols  = t['cols']
    col_list = cols.split(',')
    date_col = col_list[0] if table not in ('ggt_daily','margin') else col_list[0]
    # daily等表date_col是第二列
    if table in ('daily','daily_basic','moneyflow','stk_factor','margin_detail'):
        date_col = col_list[1]

    # 查Studio最新日期
    try:
        _, o, _ = ssh.exec_command(f"sqlite3 '{STUDIO_DB}' \"SELECT MAX({date_col}) FROM {table};\" 2>/dev/null")
        slast = o.read().decode().strip()
    except:
        slast = '19000101'
    if not slast: slast = '19000101'

    # 查Air最新
    cur = air.cursor()
    cur.execute(f"SELECT MAX({date_col}) FROM {table}")
    alast = cur.fetchone()[0]
    if not alast or str(alast) <= slast:
        log(f"  {table}: 已同步（{slast}）")
        continue

    # 导出增量
    cur.execute(f"SELECT {cols} FROM {table} WHERE {date_col} > ? ORDER BY {date_col}", [slast])
    rows = cur.fetchall()
    if not rows:
        log(f"  {table}: 无新增")
        continue

    csv_file = os.path.join(tmpdir, f"{table}.csv")
    with open(csv_file, 'w', encoding='utf-8') as f:
        for r in rows:
            f.write('|'.join(str(v) if v is not None else '' for v in r) + '\n')

    size_kb = os.path.getsize(csv_file) / 1024
    log(f"  {table}: {len(rows)}行 {size_kb:.0f}KB（{slast}→{alast}）")

    # SFTP传到Studio
    remote_csv = f"/tmp/stock_sync_{table}.csv"
    sftp.put(csv_file, remote_csv)
    os.unlink(csv_file)

    # Studio上执行导入
    ncols = len(col_list)
    import_cmd = f"""
sqlite3 '{STUDIO_DB}' << 'SQLIMPORT'
.mode list
.separator |
.import {remote_csv} {table}
.quit
SQLIMPORT
"""
    _, _, stderr = ssh.exec_command(import_cmd)
    err = stderr.read().decode()
    if err:
        log(f"    ⚠️ 导入警告: {err[:80]}")
    
    # 清理远程文件
    ssh.exec_command(f"rm -f {remote_csv}")
    
    total += len(rows)
    log(f"  ✅ {table}: +{len(rows)}行")


# 最终验证
log("--- 验证同步结果 ---")
for t, dc in [('daily','trade_date'),('daily_basic','trade_date'),('moneyflow','trade_date'),('margin','trade_date')]:
    _, o, _ = ssh.exec_command(f"sqlite3 '{STUDIO_DB}' \"SELECT MAX({dc}) FROM {t};\" 2>/dev/null")
    log(f"  Studio {t}: {o.read().decode().strip()}")



air.close()
sftp.close()
ssh.close()
os.rmdir(tmpdir)
log(f"===== 完成，共同步 {total} 行 =====")
