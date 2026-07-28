"""
方案2 主训练脚本 - Mac Studio全量版(内存优化+spawn修复)
关键修复:
  1. 多进程代码包在 if __name__=='__main__' 里(Python3.13 macOS spawn模式必须)
  2. worker按需读单票DB,不复制全量数据字典(解决内存爆炸)
调用: python main.py
"""
import sqlite3, pandas as pd, numpy as np, os, time, pickle
from multiprocessing import Pool
import warnings; warnings.filterwarnings('ignore')

# ---- worker初始化:每个子进程独立持有DB连接 ----
_wconn = None
def _init_worker(db_path):
    global _wconn
    _wconn = sqlite3.connect(db_path)

def _find_starts(close, high, dates, n, LOOKBACK, FWD_MIN, FWD_MAX, DOUBLE_THR, SEARCH_WIN, END_DATE):
    starts=[]; last_end=-1
    for T in range(LOOKBACK+1, n-FWD_MIN):
        if T<=last_end: continue
        if str(dates[T])>END_DATE: break
        end=min(T+FWD_MAX,n-1)
        wh=high[T+FWD_MIN:end+1] if T+FWD_MIN<=end else np.array([])
        if len(wh)==0: continue
        if np.max(wh)/close[T]>=DOUBLE_THR:
            lo=max(LOOKBACK+1,T-SEARCH_WIN); hi=min(n-FWD_MIN,T+SEARCH_WIN)
            tT=lo+int(np.argmin(close[lo:hi])); e2=min(tT+FWD_MAX,n-1)
            wh2=high[tT+FWD_MIN:e2+1] if tT+FWD_MIN<=e2 else np.array([])
            if len(wh2)>0 and np.max(wh2)/close[tT]>=DOUBLE_THR:
                starts.append(tT)
                last_end=tT+FWD_MIN+int(np.argmax(high[tT+FWD_MIN:e2+1]))
    return starts

def process_stock(args):
    """单票处理:读数据→找起点→构造样本,返回(pos_rows,neg_rows)"""
    code, cfg = args
    LOOKBACK=cfg['LOOKBACK']; FWD_MIN=cfg['FWD_MIN']; FWD_MAX=cfg['FWD_MAX']
    DOUBLE_THR=cfg['DOUBLE_THR']; SEARCH_WIN=cfg['SEARCH_WIN']
    START_DATE=cfg['START_DATE']; END_DATE=cfg['END_DATE']
    FEATURE_NAMES=cfg['FEATURE_NAMES']
    try:
        g=pd.read_sql(f"SELECT ts_code,trade_date,open,high,low,close,vol,amount FROM daily "
                      f"WHERE ts_code=? AND trade_date>='{START_DATE}' AND trade_date<='{END_DATE}' ORDER BY trade_date",
                      _wconn, params=[code])
        for c in ['open','high','low','close','vol','amount']: g[c]=pd.to_numeric(g[c],errors='coerce')
        g=g.dropna(subset=['close','high','low']).query('close>0 and low>0').reset_index(drop=True)
        if len(g)<LOOKBACK+FWD_MAX+2: return ([],[])
        b=pd.read_sql(f"SELECT ts_code,trade_date,turnover_rate,pe_ttm,pb,ps_ttm,total_mv,circ_mv "
                      f"FROM daily_basic WHERE ts_code=? AND trade_date>='{START_DATE}' AND trade_date<='{END_DATE}'",
                      _wconn, params=[code])
        for c in ['turnover_rate','pe_ttm','pb','ps_ttm','total_mv','circ_mv']: b[c]=pd.to_numeric(b[c],errors='coerce')
        basic_idx=b.set_index(['ts_code','trade_date'])
        mfg=pd.read_sql(f"SELECT ts_code,trade_date,buy_elg_amount,sell_elg_amount,buy_lg_amount,sell_lg_amount,net_mf_amount "
                        f"FROM moneyflow WHERE ts_code=? AND trade_date>='{START_DATE}' AND trade_date<='{END_DATE}' ORDER BY trade_date",
                        _wconn, params=[code])
        for c in ['buy_elg_amount','sell_elg_amount','buy_lg_amount','sell_lg_amount','net_mf_amount']:
            mfg[c]=pd.to_numeric(mfg[c],errors='coerce')
        mfg['elg_net']=mfg['buy_elg_amount']-mfg['sell_elg_amount']
        mfg['lg_net']=mfg['buy_lg_amount']-mfg['sell_lg_amount']
        tlg=pd.read_sql(f"SELECT trade_date,ts_code,net_amount FROM top_list "
                        f"WHERE ts_code=? AND trade_date>='20210101' AND trade_date<='{END_DATE}'",
                        _wconn, params=[code])
        tlg['net_amount']=pd.to_numeric(tlg['net_amount'],errors='coerce')
        close=g['close'].values; high=g['high'].values; dates=g['trade_date'].values; n=len(g)
        starts=_find_starts(close,high,dates,n,LOOKBACK,FWD_MIN,FWD_MAX,DOUBLE_THR,SEARCH_WIN,END_DATE)
        # 延迟import(spawn模式每个worker都会import,放这里减少顶层依赖)
        from features import make_features
        pos_rows=[]
        for T in starts:
            f=make_features(g,T,LOOKBACK,mfg,tlg,basic_idx,code,{})
            if f is None: continue
            vals=[f.get(k,np.nan) for k in FEATURE_NAMES]
            if any(v is None or (isinstance(v,float) and np.isnan(v)) for v in vals): continue
            pos_rows.append(vals+[dates[T],1])
        rng=np.random.RandomState(hash(code)&0xffffffff)
        neg_rows=[]; lo=LOOKBACK+1; hi=n-FWD_MAX-1
        if lo<hi:
            for _ in range(3):
                T=rng.randint(lo,hi); c0=close[T]; e=min(T+FWD_MAX,n-1)
                wh=high[T+FWD_MIN:e+1] if T+FWD_MIN<=e else np.array([])
                if len(wh)>0 and np.max(wh)/c0>=DOUBLE_THR: continue
                f=make_features(g,T,LOOKBACK,mfg,tlg,basic_idx,code,{})
                if f is None: continue
                vals=[f.get(k,np.nan) for k in FEATURE_NAMES]
                if any(v is None or (isinstance(v,float) and np.isnan(v)) for v in vals): continue
                neg_rows.append(vals+[dates[T],0])
        return (pos_rows, neg_rows)
    except Exception as e:
        return ([],[])


if __name__ == '__main__':
    from config import *
    from features import FEATURE_NAMES
    import lightgbm as lgb
    from sklearn.metrics import roc_auc_score
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    t0=time.time()
    print(f"{'='*60}")
    print(f"方案2 全量训练(内存优化+spawn修复) | Workers={N_WORKERS}")
    print(f"{'='*60}")

    # ---- STEP 1: 股票池 ----
    print("\n[1/7] 确定股票池...")
    conn=sqlite3.connect(DB_PATH)
    all_codes=pd.read_sql("SELECT DISTINCT ts_code FROM daily",conn)['ts_code'].tolist()
    if EXCLUDE_BJ: all_codes=[c for c in all_codes if not c.endswith('.BJ')]
    # 铁律:剔除ST+亏损股
    try:
        bl=set(pd.read_sql("SELECT ts_code FROM blacklist_st",conn)['ts_code']) | \
           set(pd.read_sql("SELECT ts_code FROM blacklist_loss",conn)['ts_code'])
        all_codes=[c for c in all_codes if c not in bl]
        print(f"  已剔除ST+亏损股 {len(bl)}只")
    except Exception as e:
        print(f"  [警告] 黑名单过滤失败: {e}")
    codes=all_codes
    stock_list=pd.read_sql("SELECT ts_code,name,industry FROM stock_list",conn)
    conn.close()
    print(f"  候选股票:{len(codes):,}只")

    # ---- STEP 2+3: 按股票并行构造样本 ----
    print(f"\n[2/7] 按股票并行构造样本(Workers={N_WORKERS})...")
    cfg={'LOOKBACK':LOOKBACK,'FWD_MIN':FWD_MIN,'FWD_MAX':FWD_MAX,'DOUBLE_THR':DOUBLE_THR,
         'SEARCH_WIN':SEARCH_WIN,'START_DATE':START_DATE,'END_DATE':END_DATE,'FEATURE_NAMES':FEATURE_NAMES}
    args_list=[(code,cfg) for code in codes]
    with Pool(N_WORKERS, initializer=_init_worker, initargs=(DB_PATH,)) as pool:
        results=pool.map(process_stock, args_list, chunksize=10)
    pos_all=[]; neg_all=[]
    for pr,nr in results: pos_all.extend(pr); neg_all.extend(nr)
    print(f"  正样本:{len(pos_all):,} 负样本候选:{len(neg_all):,}")
    np.random.seed(42)
    target_neg=len(pos_all)*3
    if len(neg_all)>target_neg:
        idx=np.random.choice(len(neg_all),target_neg,replace=False)
        neg_all=[neg_all[i] for i in idx]
    print(f"  负样本下采样后:{len(neg_all):,}")

    # ---- STEP 3: 汇总 ----
    cols=FEATURE_NAMES+['trade_date','label']
    data=pd.DataFrame(pos_all+neg_all,columns=cols)
    data['ym']=data['trade_date'].astype(str).str[:6]
    print(f"\n[3/7] 样本汇总:{len(data):,} 翻倍率:{data['label'].mean():.2%}")
    data.to_csv(os.path.join(OUTPUT_DIR,'plan2_samples.csv'),index=False)

    # ---- STEP 4: Optuna ----
    print(f"\n[4/7] Optuna超参搜索({OPTUNA_TRIALS}轮×{CV_FOLDS}折CV)...")
    train=data[data['ym']<=TRAIN_END[:6]].copy()
    test=data[data['ym']>=TEST_START[:6]].copy()
    Xtr,ytr=train[FEATURE_NAMES],train['label']
    Xte,yte=test[FEATURE_NAMES],test['label']
    print(f"  训练:{len(train):,}(正{int(ytr.sum())}) 测试:{len(test):,}(正{int(yte.sum())}) 基准:{yte.mean():.2%}")
    train_s=train.sort_values('trade_date').reset_index(drop=True)
    n=len(train_s); fsz=n//(CV_FOLDS+1)
    folds=[(train_s.index[:fsz*k], train_s.index[fsz*k:fsz*(k+1)]) for k in range(1,CV_FOLDS+1) if fsz*(k+1)<=n]
    def objective(trial):
        p=dict(objective='binary',metric='auc',verbose=-1,is_unbalance=True,num_threads=N_WORKERS,
               num_leaves=trial.suggest_int('num_leaves',15,63),
               max_depth=trial.suggest_int('max_depth',3,8),
               learning_rate=trial.suggest_float('learning_rate',0.01,0.1,log=True),
               feature_fraction=trial.suggest_float('feature_fraction',0.6,0.95),
               bagging_fraction=trial.suggest_float('bagging_fraction',0.6,0.95),
               bagging_freq=trial.suggest_int('bagging_freq',1,10),
               min_child_samples=trial.suggest_int('min_child_samples',20,100),
               lambda_l1=trial.suggest_float('lambda_l1',0,5),
               lambda_l2=trial.suggest_float('lambda_l2',0,5))
        aucs=[]
        for tri,vai in folds:
            Xt=train_s.loc[tri,FEATURE_NAMES]; yt=train_s.loc[tri,'label']
            Xv=train_s.loc[vai,FEATURE_NAMES]; yv=train_s.loc[vai,'label']
            if yt.nunique()<2 or yv.nunique()<2: continue
            m=lgb.train(p,lgb.Dataset(Xt,yt),num_boost_round=LGB_ROUNDS,
                        valid_sets=[lgb.Dataset(Xv,yv)],callbacks=[lgb.early_stopping(LGB_ES_ROUNDS,verbose=False)])
            aucs.append(roc_auc_score(yv,m.predict(Xv)))
        return np.mean(aucs) if aucs else 0.5
    study=optuna.create_study(direction='maximize')
    study.optimize(objective,n_trials=OPTUNA_TRIALS,n_jobs=1,show_progress_bar=False)
    print(f"  最佳CV AUC:{study.best_value:.4f} 参数:{study.best_params}")

    # ---- STEP 5: 最终模型 ----
    print("\n[5/7] 训练最终模型...")
    bp=dict(objective='binary',metric='auc',verbose=-1,is_unbalance=True,num_threads=N_WORKERS,**study.best_params)
    fm=lgb.train(bp,lgb.Dataset(Xtr,ytr),num_boost_round=LGB_ROUNDS,valid_sets=[lgb.Dataset(Xte,yte)],
                 callbacks=[lgb.early_stopping(LGB_ES_ROUNDS,verbose=False)])
    pred=fm.predict(Xte); auc=roc_auc_score(yte,pred)
    print(f"  ★ 测试集 AUC = {auc:.4f}")
    fm.save_model(os.path.join(OUTPUT_DIR,'plan2_model.txt'))
    with open(os.path.join(OUTPUT_DIR,'plan2_meta.pkl'),'wb') as fp:
        pickle.dump({'features':FEATURE_NAMES,'best_params':study.best_params,'test_auc':auc,'cv_auc':study.best_value},fp)

    # ---- STEP 6: 评估 ----
    print("\n[6/7] 分层回测+评估...")
    from evaluate import full_evaluation
    full_evaluation(yte.values,pred,fm,FEATURE_NAMES,OUTPUT_DIR)

    # ---- STEP 7: 选股 ----
    print("\n[7/7] 扫描当前市场...")
    from scan_market import scan_current_market_dbconn
    scan_current_market_dbconn(fm,DB_PATH,codes,stock_list,FEATURE_NAMES,OUTPUT_DIR)

    print(f"\n{'='*60}")
    print(f"全部完成! 耗时{(time.time()-t0)/60:.1f}分钟")
    print(f"{'='*60}")
