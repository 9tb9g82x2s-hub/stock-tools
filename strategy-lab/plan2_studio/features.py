"""
方案2 特征工程模块 - 丰富特征集
所有特征严格只用起点前LOOKBACK天数据(防未来函数)
特征类别:
  1. 动量类: 多周期收益率
  2. 波动类: 波动率、ATR、最大回撤
  3. 均线类: 多均线关系、粘合度、乖离率
  4. 量能类: 量比、换手率、放量特征
  5. 形态类: 价格位置、布林带位置
  6. 技术指标: MACD、RSI、KDJ
  7. 资金流: 超大单/大单/净流入(多周期)
  8. 龙虎榜: 上榜次数、净买入
  9. 估值类: PE/PB/PS/市值
"""
import numpy as np

def _ema(arr, span):
    alpha = 2.0/(span+1)
    out = np.zeros_like(arr, dtype=float)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha*arr[i] + (1-alpha)*out[i-1]
    return out

def _rsi(close, n=14):
    if len(close) < n+1: return np.nan
    diff = np.diff(close)
    gain = np.where(diff>0, diff, 0)
    loss = np.where(diff<0, -diff, 0)
    ag = np.mean(gain[-n:]); al = np.mean(loss[-n:])
    if al == 0: return 100.0
    rs = ag/al
    return 100 - 100/(1+rs)

def _kdj(high, low, close, n=9):
    if len(close) < n: return np.nan, np.nan
    rsv_list = []
    for i in range(n-1, len(close)):
        hh = np.max(high[i-n+1:i+1]); ll = np.min(low[i-n+1:i+1])
        rsv = (close[i]-ll)/(hh-ll)*100 if hh>ll else 50
        rsv_list.append(rsv)
    k = 50.0; d = 50.0
    for rsv in rsv_list:
        k = 2/3*k + 1/3*rsv
        d = 2/3*d + 1/3*k
    return k, d

def make_features(g, T, lb, mf_g, tl_g, basic_idx, code, cfg):
    """
    g: 单只票日线DataFrame(已按日期升序,reset_index)
    T: 起点位置索引
    lb: lookback天数
    返回: dict特征 或 None
    """
    i = T - 1  # 特征截止到起点前一天,起点当天不用
    if i < lb: return None
    close=g['close'].values; high=g['high'].values; low=g['low'].values
    openp=g['open'].values if 'open' in g else close
    vol=g['vol'].values; amount=g['amount'].values
    c0=close[i]; td=g['trade_date'].values[i]
    f={}

    # ---- 1. 动量类 ----
    for n in [3,5,10,20,30]:
        f[f'ret_{n}'] = c0/close[i-n]-1 if i-n>=0 and close[i-n]>0 else np.nan

    # ---- 2. 波动类 ----
    r=close[i-lb:i+1]; dr=np.diff(r)/r[:-1]
    f['vol_lb']=float(np.std(dr)) if len(dr)>0 else np.nan
    # ATR(14)
    tr_list=[]
    for j in range(max(1,i-13), i+1):
        tr=max(high[j]-low[j], abs(high[j]-close[j-1]), abs(low[j]-close[j-1]))
        tr_list.append(tr)
    f['atr14']=float(np.mean(tr_list))/c0 if c0>0 and tr_list else np.nan
    win=close[i-lb:i+1]; peak=np.maximum.accumulate(win); dd=(win-peak)/peak
    f['maxdd_lb']=float(np.min(dd))

    # ---- 3. 均线类 ----
    ma5=np.mean(close[i-5:i+1]); ma10=np.mean(close[i-10:i+1])
    ma20=np.mean(close[i-20:i+1]); ma30=np.mean(close[i-30:i+1])
    f['close_ma5']=c0/ma5-1 if ma5>0 else np.nan
    f['close_ma10']=c0/ma10-1 if ma10>0 else np.nan
    f['close_ma20']=c0/ma20-1 if ma20>0 else np.nan
    f['ma5_ma10']=ma5/ma10-1 if ma10>0 else np.nan
    f['ma5_ma20']=ma5/ma20-1 if ma20>0 else np.nan
    f['ma10_ma30']=ma10/ma30-1 if ma30>0 else np.nan
    f['ma_converge']=float(np.std([ma5,ma10,ma20,ma30])/c0) if c0>0 else np.nan

    # ---- 4. 量能类 ----
    v5=np.mean(vol[i-5:i+1]); v10=np.mean(vol[i-10:i+1]); vlb=np.mean(vol[i-lb:i+1])
    f['vol_ratio_5_lb']=v5/vlb if vlb>0 else np.nan
    f['vol_ratio_5_10']=v5/v10 if v10>0 else np.nan
    f['vol_trend']=(v5-vlb)/vlb if vlb>0 else np.nan
    # 近5日是否放量(最大单日量/均量)
    f['vol_spike']=float(np.max(vol[i-5:i+1]))/vlb if vlb>0 else np.nan

    # ---- 5. 形态类 ----
    hlb=np.max(high[i-lb:i+1]); llb=np.min(low[i-lb:i+1])
    f['price_pos_lb']=(c0-llb)/(hlb-llb) if hlb>llb else np.nan
    # 布林带位置
    mb=np.mean(close[i-20:i+1]); sd=np.std(close[i-20:i+1])
    f['boll_pos']=(c0-mb)/(2*sd) if sd>0 else np.nan

    # ---- 6. 技术指标 ----
    ema12=_ema(close[max(0,i-60):i+1],12); ema26=_ema(close[max(0,i-60):i+1],26)
    macd=ema12[-1]-ema26[-1]
    f['macd']=macd/c0 if c0>0 else np.nan
    f['rsi14']=_rsi(close[max(0,i-30):i+1],14)
    k,d=_kdj(high[max(0,i-30):i+1],low[max(0,i-30):i+1],close[max(0,i-30):i+1],9)
    f['kdj_k']=k; f['kdj_d']=d; f['kdj_diff']=k-d if not(np.isnan(k) or np.isnan(d)) else np.nan

    # ---- 7. 资金流 ----
    if mf_g is not None and len(mf_g)>0:
        pos=mf_g.index[mf_g['trade_date']<=td]
        if len(pos)>0:
            mi=pos[-1]
            for w in [5,10,20]:
                s=max(0,mi-w+1)
                f[f'elg_net_{w}']=float(mf_g['elg_net'].iloc[s:mi+1].sum())
                f[f'lg_net_{w}']=float(mf_g['lg_net'].iloc[s:mi+1].sum())
                f[f'net_mf_{w}']=float(mf_g['net_mf_amount'].iloc[s:mi+1].sum())
            amt5=float(np.sum(amount[i-4:i+1])) if i>=4 else np.nan
            f['net_mf_ratio5']=f['net_mf_5']/amt5 if amt5 and amt5>0 else np.nan
            f['elg_ratio5']=f['elg_net_5']/amt5 if amt5 and amt5>0 else np.nan
        else:
            for w in [5,10,20]: f[f'elg_net_{w}']=np.nan; f[f'lg_net_{w}']=np.nan; f[f'net_mf_{w}']=np.nan
            f['net_mf_ratio5']=np.nan; f['elg_ratio5']=np.nan
    else:
        for w in [5,10,20]: f[f'elg_net_{w}']=np.nan; f[f'lg_net_{w}']=np.nan; f[f'net_mf_{w}']=np.nan
        f['net_mf_ratio5']=np.nan; f['elg_ratio5']=np.nan

    # ---- 8. 龙虎榜 ----
    if tl_g is not None and len(tl_g)>0:
        td20=g['trade_date'].values[max(0,i-20)]
        sub=tl_g[(tl_g['trade_date']>td20)&(tl_g['trade_date']<=td)]
        f['toplist_cnt_20d']=float(len(sub))
        f['toplist_net_20d']=float(sub['net_amount'].sum()) if len(sub)>0 else 0.0
    else:
        f['toplist_cnt_20d']=0.0; f['toplist_net_20d']=0.0

    # ---- 9. 估值类 ----
    try:
        b=basic_idx.loc[(code,td)]
        f['pe_ttm']=float(b['pe_ttm']); f['pb']=float(b['pb']); f['ps_ttm']=float(b['ps_ttm'])
        f['total_mv']=float(b['total_mv']); f['circ_mv']=float(b['circ_mv'])
        f['turnover_rate']=float(b['turnover_rate'])
    except:
        f.update({'pe_ttm':np.nan,'pb':np.nan,'ps_ttm':np.nan,'total_mv':np.nan,'circ_mv':np.nan,'turnover_rate':np.nan})

    return f

FEATURE_NAMES = [
    'ret_3','ret_5','ret_10','ret_20','ret_30',
    'vol_lb','atr14','maxdd_lb',
    'close_ma5','close_ma10','close_ma20','ma5_ma10','ma5_ma20','ma10_ma30','ma_converge',
    'vol_ratio_5_lb','vol_ratio_5_10','vol_trend','vol_spike',
    'price_pos_lb','boll_pos',
    'macd','rsi14','kdj_k','kdj_d','kdj_diff',
    'elg_net_5','elg_net_10','elg_net_20','lg_net_5','lg_net_10','lg_net_20',
    'net_mf_5','net_mf_10','net_mf_20','net_mf_ratio5','elg_ratio5',
    'toplist_cnt_20d','toplist_net_20d',
    'pe_ttm','pb','ps_ttm','total_mv','circ_mv','turnover_rate',
]
