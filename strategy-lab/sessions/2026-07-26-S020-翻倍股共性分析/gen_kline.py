"""
S020 K线形态批量生成
对比三组:
  A) 翻倍最猛组(翻倍幅度TOP50)
  B) 随机翻倍组(随机抽100个)
  C) 对照组:超跌但没翻倍(随机抽100个)
每张图: 起点前40天+起点后30天K线,含均线/量能/标注起点
输出: 三个目录各存对应的PNG,再各出一张拼图总览
"""
import sqlite3,pandas as pd,numpy as np,mplfinance as mpf
import matplotlib.pyplot as plt,os,warnings
warnings.filterwarnings('ignore')
import matplotlib
matplotlib.use('Agg')

DB="/Users/ziruzhu/stock-data/stock_all.db"
OUT="/Users/ziruzhu/stock-tools/strategy-lab/sessions/S020/kline"
LOOKBACK=30; FWD_MIN,FWD_MAX=10,60; DOUBLE_THR=2.0; SEARCH_WIN=5
PLOT_PRE=40; PLOT_POST=30  # 起点前40天+后30天

os.makedirs(f"{OUT}/A_top_doubles",exist_ok=True)
os.makedirs(f"{OUT}/B_random_doubles",exist_ok=True)
os.makedirs(f"{OUT}/C_no_double_control",exist_ok=True)
np.random.seed(42)

print("加载数据...")
conn=sqlite3.connect(DB)
codes=pd.read_sql("SELECT DISTINCT ts_code FROM daily",conn)['ts_code'].tolist()
codes=[c for c in codes if not c.endswith('.BJ')]
bl=set(pd.read_sql("SELECT ts_code FROM blacklist_st",conn)['ts_code'])|set(pd.read_sql("SELECT ts_code FROM blacklist_loss",conn)['ts_code'])
codes=[c for c in codes if c not in bl]
slist=pd.read_sql("SELECT ts_code,name,industry FROM stock_list",conn)
code2name=dict(zip(slist['ts_code'],slist['name']))
code2ind=dict(zip(slist['ts_code'],slist['industry']))
daily=pd.read_sql("SELECT ts_code,trade_date,open,high,low,close,vol FROM daily WHERE ts_code IN ({}) AND trade_date>='20160101' AND trade_date<='20260630' ORDER BY ts_code,trade_date".format(','.join(['?']*min(len(codes),5000))),conn,params=codes[:5000])
for c in ['open','high','low','close','vol']: daily[c]=pd.to_numeric(daily[c],errors='coerce')
daily=daily.dropna(subset=['open','high','low','close']).query('close>0')
conn.close()
print(f"日线数据:{len(daily):,}行")

daily_g={c:g.reset_index(drop=True) for c,g in daily.groupby('ts_code')}

def find_starts_with_gain(g):
    close=g['close'].values; high=g['high'].values; dates=g['trade_date'].values; n=len(g)
    results=[]; last=-1
    for T in range(LOOKBACK+1,n-FWD_MIN):
        if T<=last: continue
        end=min(T+FWD_MAX,n-1); wh=high[T+FWD_MIN:end+1] if T+FWD_MIN<=end else np.array([])
        if len(wh)==0: continue
        if np.max(wh)/close[T]>=DOUBLE_THR:
            lo=max(LOOKBACK+1,T-SEARCH_WIN); hi=min(n-FWD_MIN,T+SEARCH_WIN)
            tT=lo+int(np.argmin(close[lo:hi])); e2=min(tT+FWD_MAX,n-1)
            wh2=high[tT+FWD_MIN:e2+1] if tT+FWD_MIN<=e2 else np.array([])
            if len(wh2)>0 and np.max(wh2)/close[tT]>=DOUBLE_THR:
                peak_gain=np.max(high[tT+FWD_MIN:e2+1])/close[tT]
                results.append((tT,peak_gain,dates[tT]))
                last=tT+FWD_MIN+int(np.argmax(high[tT+FWD_MIN:e2+1]))
    return results

print("扫描翻倍起点...")
all_starts=[]
for code,g in daily_g.items():
    if len(g)<LOOKBACK+FWD_MAX+2: continue
    for T,gain,date in find_starts_with_gain(g):
        all_starts.append({'code':code,'T':T,'gain':gain,'date':date,'name':code2name.get(code,''),'ind':code2ind.get(code,'')})
starts_df=pd.DataFrame(all_starts)
print(f"总翻倍起点:{len(starts_df)}")

# 三组
topA=starts_df.nlargest(50,'gain')
randB=starts_df.sample(min(100,len(starts_df)),random_state=42)

# 对照组:超跌但没翻倍(起点前30天-20%以上,后60天没翻倍)
ctrl=[]
code_list=list(daily_g.keys()); np.random.shuffle(code_list)
for code in code_list:
    if len(ctrl)>=100: break
    g=daily_g[code]
    if len(g)<LOOKBACK+FWD_MAX+2: continue
    lo=LOOKBACK+1; hi=len(g)-FWD_MAX-1
    if lo>=hi: continue
    for _ in range(5):
        T=np.random.randint(lo,hi)
        c0=g['close'].values[T]; c30=g['close'].values[max(0,T-30)]
        if c30<=0 or c0/c30-1>-0.15: continue  # 要求前30天跌超15%
        e=min(T+FWD_MAX,len(g)-1)
        wh=g['high'].values[T+FWD_MIN:e+1] if T+FWD_MIN<=e else np.array([])
        if len(wh)>0 and np.max(wh)/c0>=DOUBLE_THR: continue  # 排除翻倍的
        ctrl.append({'code':code,'T':T,'gain':0,'date':g['trade_date'].values[T],'name':code2name.get(code,''),'ind':code2ind.get(code,'')})
ctrl_df=pd.DataFrame(ctrl[:100])
print(f"A组:{len(topA)} B组:{len(randB)} C组:{len(ctrl_df)}")

def plot_kline(code, g, T, gain, title, save_path):
    """画单张K线图:起点前PLOT_PRE天+后PLOT_POST天"""
    start_i=max(0,T-PLOT_PRE); end_i=min(len(g)-1,T+PLOT_POST)
    sub=g.iloc[start_i:end_i+1].copy()
    if len(sub)<5: return
    sub['trade_date']=pd.to_datetime(sub['trade_date'])
    sub=sub.set_index('trade_date')
    sub.index.name='Date'
    sub.columns=[c.capitalize() for c in sub.columns]
    if not all(c in sub.columns for c in ['Open','High','Low','Close','Vol']): return
    sub=sub.rename(columns={'Vol':'Volume'})
    # 标注起点在图中的位置
    pivot_pos=T-start_i
    pivot_date=sub.index[pivot_pos] if pivot_pos<len(sub) else None
    add_vline=[]; add_title=f"{title}  [{code}]{code2name.get(code,'')}  {code2ind.get(code,'')}  gain={gain:.1f}x"
    try:
        ap=[]
        if pivot_date is not None:
            vline_vals=np.full(len(sub),np.nan); vline_vals[pivot_pos]=sub['High'].max()*1.02
            ap.append(mpf.make_addplot(vline_vals,type='scatter',markersize=200,marker='v',color='red',panel=0))
        fig,axes=mpf.plot(sub,type='candle',mav=(5,10,20),volume=True,
                          style='yahoo',addplot=ap,returnfig=True,
                          title=add_title,figsize=(14,7),
                          tight_layout=True)
        fig.savefig(save_path,dpi=80,bbox_inches='tight')
        plt.close(fig)
    except Exception as e:
        plt.close('all')

def make_grid(img_paths, grid_path, ncols=5, title=""):
    """把多张图拼成网格总览"""
    from PIL import Image
    imgs=[Image.open(p) for p in img_paths if os.path.exists(p)]
    if not imgs: return
    w,h=imgs[0].size; nrows=(len(imgs)+ncols-1)//ncols
    grid=Image.new('RGB',(w*ncols,h*nrows),(255,255,255))
    for i,img in enumerate(imgs):
        r,c=i//ncols,i%ncols; grid.paste(img,(c*w,r*h))
    grid.save(grid_path,quality=85)
    print(f"  拼图已存:{grid_path} ({len(imgs)}张)")

# 装PIL(如果没有)
try:
    from PIL import Image
except:
    import subprocess
    subprocess.run(["/Users/ziruzhu/.workbuddy/binaries/python/envs/default/bin/pip","install","Pillow","-q"])
    from PIL import Image

print("\n生成A组(翻倍最猛TOP50)...")
paths_a=[]
for i,(_,row) in enumerate(topA.iterrows()):
    p=f"{OUT}/A_top_doubles/{i+1:03d}_{row['code']}_{row['gain']:.1f}x.png"
    g=daily_g.get(row['code'])
    if g is not None:
        plot_kline(row['code'],g,int(row['T']),row['gain'],f"A翻倍TOP {row['gain']:.1f}x",p)
        if os.path.exists(p): paths_a.append(p)
    if (i+1)%10==0: print(f"  A进度:{i+1}/{len(topA)}")
make_grid(paths_a[:50],f"{OUT}/A_overview.jpg",ncols=5,title="A翻倍最猛TOP50")

print("生成B组(随机翻倍100个)...")
paths_b=[]
for i,(_,row) in enumerate(randB.iterrows()):
    p=f"{OUT}/B_random_doubles/{i+1:03d}_{row['code']}_{row['gain']:.1f}x.png"
    g=daily_g.get(row['code'])
    if g is not None:
        plot_kline(row['code'],g,int(row['T']),row['gain'],f"B随机翻倍 {row['gain']:.1f}x",p)
        if os.path.exists(p): paths_b.append(p)
    if (i+1)%20==0: print(f"  B进度:{i+1}/{len(randB)}")
make_grid(paths_b[:100],f"{OUT}/B_overview.jpg",ncols=5)

print("生成C组(超跌未翻倍对照100个)...")
paths_c=[]
for i,(_,row) in enumerate(ctrl_df.iterrows()):
    p=f"{OUT}/C_no_double_control/{i+1:03d}_{row['code']}_ctrl.png"
    g=daily_g.get(row['code'])
    if g is not None:
        plot_kline(row['code'],g,int(row['T']),0,"C超跌未翻倍对照",p)
        if os.path.exists(p): paths_c.append(p)
    if (i+1)%20==0: print(f"  C进度:{i+1}/{len(ctrl_df)}")
make_grid(paths_c[:100],f"{OUT}/C_overview.jpg",ncols=5)

print(f"\n全部完成! 产出目录:{OUT}")
print(f"  A_overview.jpg: 翻倍最猛TOP50形态总览")
print(f"  B_overview.jpg: 随机翻倍100个形态总览")
print(f"  C_overview.jpg: 超跌未翻倍对照100个")
print("  用图片查看器打开三张总览,对比规律")
