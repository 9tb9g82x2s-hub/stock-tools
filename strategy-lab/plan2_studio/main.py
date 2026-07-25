"""
方案2 主训练脚本 - Mac Studio全量版
调用: python main.py
"""
import sqlite3, pandas as pd, numpy as np, os, time, pickle
from multiprocessing import Pool, cpu_count
import warnings; warnings.filterwarnings('ignore')
from config import *
from features import make_features, FEATURE_NAMES

t0 = time.time()
print(f"{'='*60}")
print(f"方案2 全量训练 | Studio配置 | Workers={N_WORKERS}")
print(f"{'='*60}")

# ============================================================
# STEP 1: 读取所有数据到内存
# ============================================================
print("\n[1/7] 读取数据库...")
conn = sqlite3.connect(DB_PATH)
daily = pd.read_sql(f"""
SELECT ts_code,trade_date,open,high,low,close,vol,amount
FROM daily WHERE trade_date>='{START_DATE}' AND trade_date<='{DATA_LOAD_END}'
ORDER BY ts_code,trade_date""", conn)
if EXCLUDE_BJ:
    daily = daily[~daily['ts_code'].str.endswith('.BJ')]
for c in ['open','high','low','close','vol','amount']:
    daily[c] = pd.to_numeric(daily[c], errors='coerce')
daily = daily.dropna(subset=['close','high','low']).query('close>0 and low>0')

basic = pd.read_sql(f"""
SELECT ts_code,trade_date,turnover_rate,pe_ttm,pb,ps_ttm,total_mv,circ_mv
FROM daily_basic WHERE trade_date>='{START_DATE}' AND trade_date<='{DATA_LOAD_END}'""", conn)
for c in ['turnover_rate','pe_ttm','pb','ps_ttm','total_mv','circ_mv']:
    basic[c] = pd.to_numeric(basic[c], errors='coerce')

mf = pd.read_sql(f"""
SELECT ts_code,trade_date,buy_elg_amount,sell_elg_amount,
       buy_lg_amount,sell_lg_amount,net_mf_amount
FROM moneyflow WHERE trade_date>='{START_DATE}' AND trade_date<='{DATA_LOAD_END}'
ORDER BY ts_code,trade_date""", conn)
if EXCLUDE_BJ:
    mf = mf[~mf['ts_code'].str.endswith('.BJ')]
for c in ['buy_elg_amount','sell_elg_amount','buy_lg_amount','sell_lg_amount','net_mf_amount']:
    mf[c] = pd.to_numeric(mf[c], errors='coerce')
mf['elg_net'] = mf['buy_elg_amount']-mf['sell_elg_amount']
mf['lg_net']  = mf['buy_lg_amount']-mf['sell_lg_amount']

tl = pd.read_sql(f"""
SELECT trade_date,ts_code,net_amount FROM top_list
WHERE trade_date>='20210101' AND trade_date<='{DATA_LOAD_END}'""", conn)
if EXCLUDE_BJ:
    tl = tl[~tl['ts_code'].str.endswith('.BJ')]
tl['net_amount'] = pd.to_numeric(tl['net_amount'], errors='coerce')

stock_list = pd.read_sql("SELECT ts_code,name,industry FROM stock_list", conn)
conn.close()

# 过滤市值范围
basic_latest = basic.sort_values('trade_date').groupby('ts_code').last().reset_index()
valid_mv = basic_latest[(basic_latest['total_mv']>=MIN_MV*1e4) & 
                         (basic_latest['total_mv']<=MAX_MV*1e4)]['ts_code']
daily = daily[daily['ts_code'].isin(valid_mv)]

basic_idx = basic.set_index(['ts_code','trade_date'])
grouped = {code:g.reset_index(drop=True) for code,g in daily.groupby('ts_code')}
mf_grp  = {code:g.reset_index(drop=True) for code,g in mf.groupby('ts_code')}
tl_grp  = {code:g.reset_index(drop=True) for code,g in tl.groupby('ts_code')}
print(f"  股票:{len(grouped):,} 日线:{len(daily):,}")

# ============================================================
# STEP 2: 定位翻倍起点
# ============================================================
print("\n[2/7] 定位全量翻倍起点(多进程)...")
def find_starts_for_code(args):
    code, rows = args
    g = pd.DataFrame(rows, columns=['trade_date','high','low','close'])
    g = g.reset_index(drop=True)
    close=g['close'].values; high=g['high'].values; n=len(g)
    dates=g['trade_date'].values
    starts=[]; last_end=-1
    for T in range(LOOKBACK+1, n-FWD_MIN):
        if T<=last_end: continue
        # 防泄漏:起点及其翻倍窗口必须都在END_DATE之前(不用7月大跌数据做标签)
        if str(dates[T]) > END_DATE: break
        end=min(T+FWD_MAX,n-1)
        wh=high[T+FWD_MIN:end+1] if T+FWD_MIN<=end else np.array([])
        if len(wh)==0: continue
        if np.max(wh)/close[T]>=DOUBLE_THR:
            lo=max(LOOKBACK+1,T-SEARCH_WIN); hi=min(n-FWD_MIN,T+SEARCH_WIN)
            tT=lo+int(np.argmin(close[lo:hi]))
            e2=min(tT+FWD_MAX,n-1)
            wh2=high[tT+FWD_MIN:e2+1] if tT+FWD_MIN<=e2 else np.array([])
            if len(wh2)>0 and np.max(wh2)/close[tT]>=DOUBLE_THR:
                starts.append(tT)
                last_end=tT+FWD_MIN+int(np.argmax(high[tT+FWD_MIN:e2+1]))
    return code, starts

# 起点定位只用END_DATE之前的数据(选股用的7月数据不参与建标签)
args_list = [(code, g[g['trade_date']<=END_DATE][['trade_date','high','low','close']].values.tolist()) 
             for code,g in grouped.items() if len(g[g['trade_date']<=END_DATE])>=LOOKBACK+FWD_MAX+2]

with Pool(N_WORKERS) as pool:
    results = pool.map(find_starts_for_code, args_list)

pos_pts = [(code,T) for code,starts in results for T in starts]
print(f"  翻倍起点总数: {len(pos_pts):,}")

# ============================================================
# STEP 3: 构造特征(多进程)
# ============================================================
print("\n[3/7] 构造特征(多进程)...")
cfg = {}  # 占位,features.py里未使用

def build_sample(args):
    code, T, label = args
    g = grouped.get(code)
    if g is None: return None
    f = make_features(g, T, LOOKBACK, mf_grp.get(code), tl_grp.get(code), basic_idx, code, cfg)
    if f is None: return None
    vals = [f.get(k, np.nan) for k in FEATURE_NAMES]
    if any(v is None or (isinstance(v,float) and np.isnan(v)) for v in vals):
        return None
    return vals + [g['trade_date'].values[T], label]

# 正样本
pos_args = [(code, T, 1) for code, T in pos_pts]
# 负样本: 每只票随机抽,其后60天不翻倍,目标正:负=1:3
np.random.seed(42)
neg_args = []
code_list = list(grouped.keys()); np.random.shuffle(code_list)
target_neg = len(pos_pts)*3
for code in code_list:
    if len(neg_args)>=target_neg: break
    g=grouped[code]
    if len(g)<LOOKBACK+FWD_MAX+2: continue
    lo=LOOKBACK+1; hi=len(g)-FWD_MAX-1
    if lo>=hi: continue
    for _ in range(3):
        T=np.random.randint(lo,hi)
        c0=g['close'].values[T]; e=min(T+FWD_MAX,len(g)-1)
        wh=g['high'].values[T+FWD_MIN:e+1] if T+FWD_MIN<=e else np.array([])
        if len(wh)>0 and np.max(wh)/c0>=DOUBLE_THR: continue
        neg_args.append((code,T,0))

print(f"  正样本:{len(pos_args):,} 负样本:{len(neg_args):,}")

all_args = pos_args + neg_args
with Pool(N_WORKERS) as pool:
    samples = pool.map(build_sample, all_args)
samples = [s for s in samples if s is not None]

cols = FEATURE_NAMES + ['trade_date','label']
data = pd.DataFrame(samples, columns=cols)
data['ym'] = data['trade_date'].astype(str).str[:6]
print(f"  有效样本:{len(data):,} 翻倍率:{data['label'].mean():.2%}")
data.to_csv(os.path.join(OUTPUT_DIR,'plan2_samples.csv'), index=False)
print(f"  样本已存: plan2_samples.csv")

# ============================================================
# STEP 4: 时间切分 + Optuna超参搜索
# ============================================================
print("\n[4/7] Optuna超参搜索(利用多核)...")
import lightgbm as lgb
from sklearn.metrics import roc_auc_score
import optuna
optuna.logging.set_verbosity(optuna.logging.WARNING)

train = data[data['ym']<=TRAIN_END[:6]].copy()
test  = data[data['ym']>=TEST_START[:6]].copy()
Xtr, ytr = train[FEATURE_NAMES], train['label']
Xte, yte = test[FEATURE_NAMES], test['label']
print(f"  训练:{len(train):,}(正{int(ytr.sum())}) 测试:{len(test):,}(正{int(yte.sum())}) 测试基准:{yte.mean():.2%}")

# 时间序列CV: 训练集内按时间再分5折
train_sorted = train.sort_values('trade_date').reset_index(drop=True)
def time_series_folds(df, n_folds):
    n=len(df); fold_size=n//(n_folds+1); folds=[]
    for k in range(1,n_folds+1):
        tr_idx=df.index[:fold_size*k]; va_idx=df.index[fold_size*k:fold_size*(k+1)]
        if len(va_idx)>0: folds.append((tr_idx,va_idx))
    return folds
folds = time_series_folds(train_sorted, CV_FOLDS)

def objective(trial):
    params = dict(
        objective='binary', metric='auc', verbose=-1, is_unbalance=True,
        num_leaves=trial.suggest_int('num_leaves',15,63),
        max_depth=trial.suggest_int('max_depth',3,8),
        learning_rate=trial.suggest_float('learning_rate',0.01,0.1,log=True),
        feature_fraction=trial.suggest_float('feature_fraction',0.6,0.95),
        bagging_fraction=trial.suggest_float('bagging_fraction',0.6,0.95),
        bagging_freq=trial.suggest_int('bagging_freq',1,10),
        min_child_samples=trial.suggest_int('min_child_samples',20,100),
        lambda_l1=trial.suggest_float('lambda_l1',0,5),
        lambda_l2=trial.suggest_float('lambda_l2',0,5),
    )
    aucs=[]
    for tr_idx,va_idx in folds:
        Xt=train_sorted.loc[tr_idx,FEATURE_NAMES]; yt=train_sorted.loc[tr_idx,'label']
        Xv=train_sorted.loc[va_idx,FEATURE_NAMES]; yv=train_sorted.loc[va_idx,'label']
        if yt.nunique()<2 or yv.nunique()<2: continue
        m=lgb.train(params, lgb.Dataset(Xt,yt), num_boost_round=LGB_ROUNDS,
                    valid_sets=[lgb.Dataset(Xv,yv)],
                    callbacks=[lgb.early_stopping(LGB_ES_ROUNDS,verbose=False)])
        aucs.append(roc_auc_score(yv, m.predict(Xv)))
    return np.mean(aucs) if aucs else 0.5

study = optuna.create_study(direction='maximize')
study.optimize(objective, n_trials=OPTUNA_TRIALS, n_jobs=1, show_progress_bar=False)
print(f"  最佳CV AUC:{study.best_value:.4f}")
print(f"  最佳参数:{study.best_params}")

# ============================================================
# STEP 5: 用最佳参数训练最终模型
# ============================================================
print("\n[5/7] 训练最终模型...")
best_params = dict(objective='binary', metric='auc', verbose=-1, is_unbalance=True,
                   num_threads=N_WORKERS, **study.best_params)
final_model = lgb.train(best_params, lgb.Dataset(Xtr,ytr), num_boost_round=LGB_ROUNDS,
                        valid_sets=[lgb.Dataset(Xte,yte)],
                        callbacks=[lgb.early_stopping(LGB_ES_ROUNDS,verbose=False)])
pred_te = final_model.predict(Xte)
test_auc = roc_auc_score(yte, pred_te)
print(f"  ★ 测试集 AUC = {test_auc:.4f}")
final_model.save_model(os.path.join(OUTPUT_DIR,'plan2_model.txt'))
with open(os.path.join(OUTPUT_DIR,'plan2_meta.pkl'),'wb') as fp:
    pickle.dump({'features':FEATURE_NAMES,'best_params':study.best_params,
                 'test_auc':test_auc,'cv_auc':study.best_value}, fp)

# ============================================================
# STEP 6: 分层回测 + 评估
# ============================================================
print("\n[6/7] 分层回测+评估...")
from evaluate import full_evaluation
full_evaluation(yte.values, pred_te, final_model, FEATURE_NAMES, OUTPUT_DIR)

# ============================================================
# STEP 7: 扫描当前市场,输出候选股
# ============================================================
print("\n[7/7] 扫描当前市场输出候选股...")
from scan_market import scan_current_market
scan_current_market(final_model, grouped, mf_grp, tl_grp, basic_idx,
                    stock_list, FEATURE_NAMES, OUTPUT_DIR)

print(f"\n{'='*60}")
print(f"全部完成! 耗时{(time.time()-t0)/60:.1f}分钟")
print(f"产出: plan2_model.txt(模型) plan2_samples.csv(样本)")
print(f"      evaluation_report.txt(评估) candidates.csv(候选股)")
print(f"{'='*60}")
