"""
选股模块: 扫描当前市场,输出"最像翻倍起点"的候选股票
输出两份:
  1. 全量版: candidates.csv / candidates_top200.csv
  2. 喜神池过滤版: candidates_xishen.csv (USE_XISHEN_FILTER=True时)
注意: 训练始终用全量池(保证模型能力),喜神池只在选股环节过滤
"""
import numpy as np, pandas as pd, os
from config import SCORE_DATE, TOP_N, SCORE_THRESHOLD, LOOKBACK
from config import USE_XISHEN_FILTER, XISHEN_POOL_FILE
from features import make_features, FEATURE_NAMES

def _print_table(df, title):
    print(f"\n{'='*68}")
    print(f"{title}")
    print(f"{'='*68}")
    print(f"{'排名':<5}{'代码':<13}{'名称':<10}{'行业':<12}{'现价':>7}{'得分':>8}  {'喜神':>6}  {'评级'}")
    print("-"*68)
    for idx, (_, row) in enumerate(df.iterrows()):
        lvl  = "★★★" if row['score']>=0.55 else "★★" if row['score']>=0.45 else "★"
        name = str(row.get('name',''))[:6]
        ind  = str(row.get('industry',''))[:8]
        xs   = "✅" if row.get('in_xishen') else "  "
        print(f"{idx+1:<5}{row['ts_code']:<13}{name:<10}{ind:<12}{row['close']:>7.2f}{row['score']:>8.4f}  {xs:<6}  {lvl}")

def scan_current_market(model, grouped, mf_grp, tl_grp, basic_idx, stock_list, feat_names, output_dir):
    print(f"  扫描日期: {SCORE_DATE}")

    # ---- 加载喜神池 ----
    xishen_codes = set()
    if USE_XISHEN_FILTER:
        xp = os.path.join(os.path.dirname(os.path.abspath(__file__)), XISHEN_POOL_FILE)
        if os.path.exists(xp):
            xdf = pd.read_csv(xp)
            xishen_codes = set(xdf['ts_code'].tolist())
            print(f"  喜神池已加载: {len(xishen_codes)}只 ({XISHEN_POOL_FILE})")
        else:
            print(f"  [警告] 喜神池文件不存在: {xp}, 跳过过滤")

    # ---- 特征打分 ----
    records = []
    for code, g in grouped.items():
        idx = g.index[g['trade_date'] == SCORE_DATE]
        if len(idx) == 0: continue
        T = int(idx[0])
        if T < LOOKBACK + 1: continue
        f = make_features(g, T, LOOKBACK, mf_grp.get(code), tl_grp.get(code), basic_idx, code, {})
        if f is None: continue
        vals = [f.get(k, np.nan) for k in feat_names]
        if any(v is None or (isinstance(v, float) and np.isnan(v)) for v in vals): continue
        records.append({'ts_code': code, 'feat_vals': vals, 'close': g['close'].values[T]})

    if not records:
        print("  没有找到有效记录,检查SCORE_DATE是否为交易日"); return

    Xscore = pd.DataFrame([r['feat_vals'] for r in records], columns=feat_names)
    scores  = model.predict(Xscore)
    out = pd.DataFrame({
        'ts_code': [r['ts_code'] for r in records],
        'close':   [r['close']   for r in records],
        'score':   scores,
    })
    out = out.merge(stock_list[['ts_code','name','industry']], on='ts_code', how='left')
    out['in_xishen'] = out['ts_code'].isin(xishen_codes)
    out = out.sort_values('score', ascending=False).reset_index(drop=True)

    # ---- 全量版输出 ----
    out_full = out[out['score'] >= SCORE_THRESHOLD].head(TOP_N)
    xishen_cnt = out_full['in_xishen'].sum()
    print(f"\n  全市场覆盖:{len(out):,}只 | score≥{SCORE_THRESHOLD}:{len(out_full)}只 | 其中喜神池:{xishen_cnt}只")
    _print_table(out_full, f"全量候选股 TOP{TOP_N} (截至{SCORE_DATE})")
    out_full.to_csv(os.path.join(output_dir, 'candidates.csv'), index=False)
    out.head(200).to_csv(os.path.join(output_dir, 'candidates_top200.csv'), index=False)

    # ---- 喜神池过滤版输出 ----
    if USE_XISHEN_FILTER and xishen_codes:
        out_xs = out[out['in_xishen'] & (out['score'] >= SCORE_THRESHOLD)].head(TOP_N).copy()
        xs_total = out[out['in_xishen']]
        print(f"\n  喜神池覆盖:{len(xs_total)}只 | score≥{SCORE_THRESHOLD}:{len(out_xs)}只")
        if len(out_xs) > 0:
            _print_table(out_xs, f"喜神池候选股 TOP{TOP_N} (截至{SCORE_DATE}) ✅")
        else:
            print("  喜神池内无score达标的候选,尝试降低SCORE_THRESHOLD或扩大喜神池")
            # 降阈值后再试一次
            fallback = out[out['in_xishen']].head(20)
            if len(fallback) > 0:
                _print_table(fallback, f"喜神池候选股 TOP20 (降低阈值) ✅")
        out_xs.to_csv(os.path.join(output_dir, 'candidates_xishen.csv'), index=False)
        print(f"\n  候选股已存: candidates.csv(全量TOP{TOP_N}) / candidates_xishen.csv(喜神过滤) / candidates_top200.csv")
    else:
        print(f"\n  候选股已存: candidates.csv(TOP{TOP_N}) / candidates_top200.csv")
