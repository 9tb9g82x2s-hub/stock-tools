#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""持有周期(1/2/3/5天)网格结果统一分析 + combined重算 + 数据自查"""
import json, numpy as np, pandas as pd, os

DIR = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-26-S023-周度5天换仓"
STOP_LOSS_GRID = [-0.06, -0.08, -0.10, -0.12, -0.15, None]

def recompute_combined(singles, n_phase, hold_days):
    """前向填充对齐后等权合并（修正版）"""
    out = []
    for sl in STOP_LOSS_GRID:
        subs = [s for s in singles if s['stop_loss']==sl]
        if len(subs) < n_phase:
            continue
        all_d = sorted(set(c['date'] for s in subs for c in s['nav_curve']))
        aligned = []
        for s in subs:
            cd = {c['date']: c['nav'] for c in s['nav_curve']}
            seq, last = [], 1.0
            for dt in all_d:
                if dt in cd: last = cd[dt]
                seq.append(last)
            aligned.append(seq)
        comb = np.array(aligned).mean(axis=0)
        n_years = (pd.to_datetime(all_d[-1])-pd.to_datetime(all_d[0])).days/365.25
        ann = comb[-1]**(1/n_years)-1 if n_years>0 and comb[-1]>0 else 0
        arr = np.concatenate([[1.0], comb])
        dd = (arr/np.maximum.accumulate(arr)-1).min()
        dret = np.diff(arr)/arr[:-1]
        sh = float(dret.mean()/dret.std()*np.sqrt(252/hold_days)) if dret.std()>0 else 0
        out.append({'stop_loss':sl,'annual_return':round(ann,4),
                    'max_drawdown':round(float(dd),4),'sharpe':round(sh,4),
                    'total_return':round(float(comb[-1]-1),4)})
    return out

print("="*80)
print("S023 持有周期网格分析（1/2/3/5天）")
print("="*80)

summary = {}
for hd in [1, 2, 3, 5]:
    f = f"{DIR}/s023_grid_{hd}d_result.json"
    if not os.path.exists(f):
        print(f"\n{hd}天版：文件缺失"); continue
    d = json.load(open(f))
    singles = d['single_strategies']
    n_phase = d['config']['n_phases']

    # === 数据自查：扫描所有期的极端收益 ===
    max_pr, n_extreme = 0, 0
    for s in singles:
        for t in s.get('nav_curve', []):
            pass
    # 用nav相邻比值近似期收益，扫描>30%
    extreme_found = []
    for s in singles:
        navs = [1.0]+[c['nav'] for c in s['nav_curve']]
        for i in range(1, len(navs)):
            r = navs[i]/navs[i-1]-1
            if r > 0.30:
                extreme_found.append((s['phase'], s['stop_loss'], s['nav_curve'][i-1]['date'], r))
    print(f"\n{'='*80}")
    print(f"【{hd}天版】数据自查：单期>30%极端值 {len(extreme_found)}个", 
          "✓干净" if not extreme_found else "⚠️需排查")
    if extreme_found[:3]:
        for e in extreme_found[:3]:
            print(f"    Phase{e[0]} SL={e[1]} 日期{e[2]} 收益{e[3]*100:.0f}%")

    # === 各相位最优（按夏普） ===
    best = max(singles, key=lambda x: x['sharpe'])
    sl_tag = f"{best['stop_loss']*100:.0f}%" if best['stop_loss'] else 'None'
    print(f"  最优单策略: Phase{best['phase']} SL={sl_tag} → 年化{best['annual_return']*100:.1f}% "
          f"回撤{best['max_drawdown']*100:.1f}% 夏普{best['sharpe']:.2f} 胜率{best['win_rate']*100:.1f}%")

    # === 重算combined ===
    comb = recompute_combined(singles, n_phase, hd)
    if comb:
        best_c = max(comb, key=lambda x: x['sharpe'])
        sl_tag2 = f"{best_c['stop_loss']*100:.0f}%" if best_c['stop_loss'] else 'None'
        print(f"  最优组合({n_phase}phase): SL={sl_tag2} → 年化{best_c['annual_return']*100:.1f}% "
              f"回撤{best_c['max_drawdown']*100:.1f}% 夏普{best_c['sharpe']:.2f}")
    else:
        best_c = None
        print(f"  组合: 无（{n_phase}phase不足）")

    # 更新结果文件的combined
    d['combined_strategies'] = comb
    json.dump(d, open(f,'w'), ensure_ascii=False, indent=2, default=str)

    summary[hd] = {'best_single': best, 'best_combined': best_c, 'n_phase': n_phase}

# === 持有周期横向对比 ===
print(f"\n{'='*80}")
print("【持有周期横向对比】各版本最优单策略")
print(f"{'持有':>5}{'相位':>5}{'止损':>7}{'年化':>9}{'回撤':>9}{'夏普':>7}{'胜率':>7}")
for hd in [1,2,3,5]:
    if hd not in summary: continue
    b = summary[hd]['best_single']
    sl = f"{b['stop_loss']*100:.0f}%" if b['stop_loss'] else 'None'
    print(f"{hd}天{'':>3}P{b['phase']:>3}{sl:>7}{b['annual_return']*100:>8.1f}%"
          f"{b['max_drawdown']*100:>8.1f}%{b['sharpe']:>7.2f}{b['win_rate']*100:>6.1f}%")
print(f"\n参照 S019(10日): 年化64.5% 回撤-12.2% 夏普2.17 胜率70.0%")

# 保存汇总供报告用
json.dump({str(k):{'best_single':v['best_single'],
                   'best_combined':v['best_combined'],'n_phase':v['n_phase']} 
           for k,v in summary.items()},
          open(f"{DIR}/holdperiod_summary.json",'w'), ensure_ascii=False, indent=2, default=str)
print(f"\n汇总已存 holdperiod_summary.json")
