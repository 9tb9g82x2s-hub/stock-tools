"""
评估模块: 分层回测 + IC + 混淆矩阵 + 特征重要性
"""
import numpy as np, pandas as pd, os
from sklearn.metrics import roc_auc_score, confusion_matrix, precision_recall_curve

def full_evaluation(y_true, y_pred, model, feat_names, output_dir):
    lines=[]
    def log(s): print("  "+s); lines.append(s)

    auc=roc_auc_score(y_true,y_pred)
    base=y_true.mean()
    log(f"测试集 AUC = {auc:.4f}")
    log(f"测试集基准翻倍率 = {base:.2%}")

    # ---- 分层回测: 按预测分数分10层,看各层实际翻倍率 ----
    log("\n=== 分层回测(按预测概率分10档) ===")
    df=pd.DataFrame({'y':y_true,'p':y_pred})
    df['decile']=pd.qcut(df['p'],10,labels=False,duplicates='drop')
    log(f"{'档位':<6}{'样本数':<8}{'实际翻倍率':<12}{'相对基准提升'}")
    grp=df.groupby('decile')
    for dec in sorted(df['decile'].unique(),reverse=True):
        sub=df[df['decile']==dec]
        rate=sub['y'].mean(); lift=rate/base if base>0 else 0
        log(f"D{int(dec):<5}{len(sub):<8}{rate:<12.2%}{lift:.2f}倍")
    # 单调性检验
    dec_rates=grp['y'].mean()
    mono = dec_rates.is_monotonic_increasing
    log(f"分层单调性(概率越高翻倍率越高): {'✓单调' if mono else '部分单调'}")
    top_rate=df[df['decile']==df['decile'].max()]['y'].mean()
    log(f"最高档翻倍率 {top_rate:.2%} vs 基准 {base:.2%} → 提升{top_rate/base:.2f}倍")

    # ---- 多阈值precision/recall ----
    log("\n=== 多阈值表现 ===")
    prec,rec,thr=precision_recall_curve(y_true,y_pred)
    f1=2*prec*rec/(prec+rec+1e-9); bi=np.argmax(f1)
    bt=thr[bi] if bi<len(thr) else 0.5
    for th in [0.3,0.4,0.5,bt]:
        pb=(y_pred>=th).astype(int); cm=confusion_matrix(y_true,pb)
        if cm.shape==(2,2):
            tp=cm[1,1];fp=cm[0,1];fn=cm[1,0]
            p=tp/(tp+fp) if tp+fp>0 else 0; r=tp/(tp+fn) if tp+fn>0 else 0
            tag=" (最佳F1)" if abs(th-bt)<1e-6 else ""
            log(f"阈值{th:.3f}: precision={p:.2%} recall={r:.2%} 提升{p/base:.2f}倍{tag}")

    # ---- 特征重要性 ----
    log("\n=== 特征重要性 TOP20(gain) ===")
    imp=pd.DataFrame({'feature':feat_names,'gain':model.feature_importance('gain')})
    imp=imp.sort_values('gain',ascending=False)
    for _,r in imp.head(20).iterrows():
        log(f"{r['feature']:<20}{r['gain']:.0f}")
    imp.to_csv(os.path.join(output_dir,'feature_importance.csv'),index=False)

    with open(os.path.join(output_dir,'evaluation_report.txt'),'w') as f:
        f.write("\n".join(lines))
    print("  评估报告已存: evaluation_report.txt")
