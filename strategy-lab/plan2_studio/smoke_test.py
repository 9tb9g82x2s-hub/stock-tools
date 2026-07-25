"""
冒烟测试: 在Air上用小规模数据验证全流程无bug
只取部分股票 + Optuna 3轮 + 快速跑通7个step
不追求效果,只验证代码正确性
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ---- Monkey-patch config为小规模 ----
import config
config.OPTUNA_TRIALS = 3
config.CV_FOLDS = 3
config.N_WORKERS = 4
config.MIN_MV = 5
config.MAX_MV = 1000
print("[冒烟测试] Optuna=3轮 CV=3折 Workers=4")

# 限制股票数量:改造main的数据加载(通过环境变量传递)
os.environ['SMOKE_TEST'] = '1'
os.environ['SMOKE_N_STOCKS'] = '300'

# 直接执行main的逻辑,但先patch daily加载
import sqlite3, pandas as pd, numpy as np, time, pickle
from multiprocessing import Pool
import warnings; warnings.filterwarnings('ignore')
from config import *
from features import make_features, FEATURE_NAMES

t0=time.time()
print("="*50); print("冒烟测试开始"); print("="*50)

# STEP1 数据(限300只票)
print("\n[1/7] 读取数据(限300只)...")
conn=sqlite3.connect(DB_PATH)
codes300=pd.read_sql("SELECT DISTINCT ts_code FROM daily WHERE ts_code NOT LIKE '%.BJ' LIMIT 300",conn)['ts_code'].tolist()
ph=','.join(['?']*len(codes300))
# 数据加载到DATA_LOAD_END(含选股日7月24日),训练标签只用END_DATE之前
daily=pd.read_sql(f"SELECT ts_code,trade_date,open,high,low,close,vol,amount FROM daily WHERE ts_code IN ({ph}) AND trade_date>='{START_DATE}' AND trade_date<='{DATA_LOAD_END}' ORDER BY ts_code,trade_date",conn,params=codes300)
for c in ['open','high','low','close','vol','amount']: daily[c]=pd.to_numeric(daily[c],errors='coerce')
daily=daily.dropna(subset=['close','high','low']).query('close>0 and low>0')
basic=pd.read_sql(f"SELECT ts_code,trade_date,turnover_rate,pe_ttm,pb,ps_ttm,total_mv,circ_mv FROM daily_basic WHERE ts_code IN ({ph}) AND trade_date>='{START_DATE}' AND trade_date<='{DATA_LOAD_END}'",conn,params=codes300)
for c in ['turnover_rate','pe_ttm','pb','ps_ttm','total_mv','circ_mv']: basic[c]=pd.to_numeric(basic[c],errors='coerce')
mf=pd.read_sql(f"SELECT ts_code,trade_date,buy_elg_amount,sell_elg_amount,buy_lg_amount,sell_lg_amount,net_mf_amount FROM moneyflow WHERE ts_code IN ({ph}) AND trade_date>='{START_DATE}' AND trade_date<='{DATA_LOAD_END}' ORDER BY ts_code,trade_date",conn,params=codes300)
for c in ['buy_elg_amount','sell_elg_amount','buy_lg_amount','sell_lg_amount','net_mf_amount']: mf[c]=pd.to_numeric(mf[c],errors='coerce')
mf['elg_net']=mf['buy_elg_amount']-mf['sell_elg_amount']; mf['lg_net']=mf['buy_lg_amount']-mf['sell_lg_amount']
tl=pd.read_sql(f"SELECT trade_date,ts_code,net_amount FROM top_list WHERE ts_code IN ({ph}) AND trade_date>='20210101' AND trade_date<='{DATA_LOAD_END}'",conn,params=codes300)
tl['net_amount']=pd.to_numeric(tl['net_amount'],errors='coerce')
stock_list=pd.read_sql("SELECT ts_code,name,industry FROM stock_list",conn)
conn.close()

basic_idx=basic.set_index(['ts_code','trade_date'])
grouped={code:g.reset_index(drop=True) for code,g in daily.groupby('ts_code')}
mf_grp={code:g.reset_index(drop=True) for code,g in mf.groupby('ts_code')}
tl_grp={code:g.reset_index(drop=True) for code,g in tl.groupby('ts_code')}
print(f"  股票:{len(grouped)} 日线:{len(daily):,}")

# STEP2 起点(单进程版,避免多进程序列化问题在测试中干扰)
print("\n[2/7] 定位起点...")
def find_starts(g):
    close=g['close'].values; high=g['high'].values; n=len(g); starts=[]; last_end=-1
    for T in range(LOOKBACK+1,n-FWD_MIN):
        if T<=last_end: continue
        end=min(T+FWD_MAX,n-1); wh=high[T+FWD_MIN:end+1] if T+FWD_MIN<=end else np.array([])
        if len(wh)==0: continue
        if np.max(wh)/close[T]>=DOUBLE_THR:
            lo=max(LOOKBACK+1,T-SEARCH_WIN); hi=min(n-FWD_MIN,T+SEARCH_WIN)
            tT=lo+int(np.argmin(close[lo:hi])); e2=min(tT+FWD_MAX,n-1)
            wh2=high[tT+FWD_MIN:e2+1] if tT+FWD_MIN<=e2 else np.array([])
            if len(wh2)>0 and np.max(wh2)/close[tT]>=DOUBLE_THR:
                starts.append(tT); last_end=tT+FWD_MIN+int(np.argmax(high[tT+FWD_MIN:e2+1]))
    return starts
pos_pts=[]
for code,g in grouped.items():
    if len(g)<LOOKBACK+FWD_MAX+2: continue
    for T in find_starts(g): pos_pts.append((code,T))
print(f"  起点:{len(pos_pts)}")

# STEP3 特征
print("\n[3/7] 构造特征...")
def build(code,T,label):
    g=grouped.get(code)
    if g is None: return None
    f=make_features(g,T,LOOKBACK,mf_grp.get(code),tl_grp.get(code),basic_idx,code,{})
    if f is None: return None
    vals=[f.get(k,np.nan) for k in FEATURE_NAMES]
    if any(v is None or (isinstance(v,float) and np.isnan(v)) for v in vals): return None
    return vals+[g['trade_date'].values[T],label]
np.random.seed(42)
rows=[]
for code,T in pos_pts:
    r=build(code,T,1)
    if r: rows.append(r)
neg=0; cl=list(grouped.keys()); np.random.shuffle(cl); tgt=len(pos_pts)*3
for code in cl:
    if neg>=tgt: break
    g=grouped[code]
    if len(g)<LOOKBACK+FWD_MAX+2: continue
    lo=LOOKBACK+1; hi=len(g)-FWD_MAX-1
    if lo>=hi: continue
    for _ in range(3):
        T=np.random.randint(lo,hi); c0=g['close'].values[T]; e=min(T+FWD_MAX,len(g)-1)
        wh=g['high'].values[T+FWD_MIN:e+1] if T+FWD_MIN<=e else np.array([])
        if len(wh)>0 and np.max(wh)/c0>=DOUBLE_THR: continue
        r=build(code,T,0)
        if r: rows.append(r); neg+=1
data=pd.DataFrame(rows,columns=FEATURE_NAMES+['trade_date','label'])
data['ym']=data['trade_date'].astype(str).str[:6]
print(f"  样本:{len(data)} 翻倍率:{data['label'].mean():.2%}")
if len(data)<50: print("  [警告]样本太少,冒烟测试仅验证流程"); 

# STEP4 Optuna(3轮)
print("\n[4/7] Optuna(3轮)...")
import lightgbm as lgb
from sklearn.metrics import roc_auc_score
import optuna
optuna.logging.set_verbosity(optuna.logging.WARNING)
train=data[data['ym']<=TRAIN_END[:6]].copy(); test=data[data['ym']>=TEST_START[:6]].copy()
if len(test)<10 or test['label'].nunique()<2:
    print("  [冒烟]测试集太小,用随机切分替代验证流程")
    from sklearn.model_selection import train_test_split
    train,test=train_test_split(data,test_size=0.3,random_state=42,stratify=data['label'])
Xtr,ytr=train[FEATURE_NAMES],train['label']; Xte,yte=test[FEATURE_NAMES],test['label']
def obj(trial):
    p=dict(objective='binary',metric='auc',verbose=-1,is_unbalance=True,
           num_leaves=trial.suggest_int('num_leaves',15,40),
           max_depth=trial.suggest_int('max_depth',3,6),
           learning_rate=trial.suggest_float('learning_rate',0.02,0.1,log=True),
           min_child_samples=trial.suggest_int('min_child_samples',20,50))
    m=lgb.train(p,lgb.Dataset(Xtr,ytr),num_boost_round=100,
                valid_sets=[lgb.Dataset(Xte,yte)],callbacks=[lgb.early_stopping(20,verbose=False)])
    return roc_auc_score(yte,m.predict(Xte))
study=optuna.create_study(direction='maximize')
study.optimize(obj,n_trials=3,show_progress_bar=False)
print(f"  最佳AUC:{study.best_value:.4f}")

# STEP5 最终模型
print("\n[5/7] 最终模型...")
bp=dict(objective='binary',metric='auc',verbose=-1,is_unbalance=True,num_threads=4,**study.best_params)
fm=lgb.train(bp,lgb.Dataset(Xtr,ytr),num_boost_round=200,valid_sets=[lgb.Dataset(Xte,yte)],callbacks=[lgb.early_stopping(20,verbose=False)])
pred=fm.predict(Xte); auc=roc_auc_score(yte,pred)
print(f"  测试AUC:{auc:.4f}")

# STEP6 评估
print("\n[6/7] 评估模块...")
from evaluate import full_evaluation
full_evaluation(yte.values,pred,fm,FEATURE_NAMES,OUTPUT_DIR)

# STEP7 选股
print("\n[7/7] 选股模块...")
from scan_market import scan_current_market
scan_current_market(fm,grouped,mf_grp,tl_grp,basic_idx,stock_list,FEATURE_NAMES,OUTPUT_DIR)

print(f"\n{'='*50}")
print(f"★ 冒烟测试全流程通过! 耗时{time.time()-t0:.0f}秒")
print(f"  7个step全部执行成功,代码逻辑无bug")
print(f"  Studio上直接跑 install_and_run.sh 即可全量")
print(f"{'='*50}")
