"""
方案2 Studio配置文件
Mac Studio专属配置 - 充分利用高配算力
"""
import multiprocessing, os

# ========== 数据路径 ==========
# Studio上同步后修改这里
DB_PATH = "/Users/ziruzhu/stock-data/stock_all.db"
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))

# ========== 数据范围 ==========
START_DATE      = "20160101"
END_DATE        = "20260630"   # 样本标签截止(避开2026.7大跌,不用来构造训练/测试标签)
DATA_LOAD_END   = "20260724"   # 数据加载截止(含选股日当天,比END_DATE晚)
TRAIN_END       = "20231231"   # 2016-2023训练
TEST_START      = "20240101"   # 2024-2026.6测试

# ========== 核心参数(已调参锁定) ==========
LOOKBACK   = 30   # 特征窗口:起点前30天 (Air调参结论:不敏感,30天最优)
FWD_MIN    = 10   # 起点后至少10天翻倍
FWD_MAX    = 60   # 起点后最多60天翻倍
DOUBLE_THR = 2.0  # 翻倍阈值:high >= close * 2.0
SEARCH_WIN = 5    # 起点精确定位:在候选点±5天找最低价

# ========== Studio算力配置 ==========
N_WORKERS = max(8, multiprocessing.cpu_count() - 2)  # 留2个核给系统
OPTUNA_TRIALS = 100       # 超参搜索轮数 (Air用固定参数,Studio搜100轮)
CV_FOLDS = 5              # 时间序列5折交叉验证
LGB_ROUNDS = 3000         # 最大树数量 (early stopping控制)
LGB_ES_ROUNDS = 80        # early stopping耐心

# ========== 过滤条件 ==========
EXCLUDE_BJ = True         # 剔除北交所
MIN_MV = 5                # 最小市值(亿元),剔除超小盘垃圾
MAX_MV = 1000             # 最大市值(亿元),超大盘翻倍概率极低
MIN_PRICE = 1.0           # 剔除1元以下仙股

# ========== 选股输出 ==========
SCORE_DATE = "20260724"   # 扫描当前市场这一天
TOP_N = 50                # 输出前N只候选股
SCORE_THRESHOLD = 0.35    # 输出分数阈值

# ========== 喜神池过滤(叠加命理择股) ==========
# 训练始终用全量池(保证模型能力),仅在选股时可选叠加喜神池过滤
USE_XISHEN_FILTER = True                      # 是否额外输出喜神池过滤版
XISHEN_POOL_FILE  = "xishen_plus_pool.csv"    # 3074只宽松版(喜神+对紫儒有利)

print(f"Studio配置加载完成 | Workers={N_WORKERS} | Optuna={OPTUNA_TRIALS}轮 | CV={CV_FOLDS}折")
