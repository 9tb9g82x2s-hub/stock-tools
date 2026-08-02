# -*- coding: utf-8 -*-
"""S019 配置：10天持有，喜神池LightGBM选股（33因子）"""

# ===== 策略参数 =====
HOLD_DAYS   = 10      # 持有交易日数（含首尾）：T+1开盘买，T+10收盘卖
REBAL_FREQ  = 10      # 换仓频率=phase数（每10天换一次，10个错位相位）
TOP_N       = 20     # 选股数量
TRAIN_MONTHS= 12     # 滚动训练窗口
STOP_LOSS_GRID = [-0.06, -0.08, -0.10, -0.12, -0.15, None]
PRICE_LIMIT = 500    # 股价上限
USE_TIME_FEATS = False

# ===== 数据范围 =====
BACKTEST_START = "20170101"
DATA_END    = "20260730"
OOS_HOLDOUT_START = "20240101"  # 封存期起始（2024-2026样本外验证）

# ===== 交易成本 =====
BC = 0.00025   # 买入佣金 万2.5
SC = 0.00125   # 卖出佣金+印花税 万12.5

# ===== 33个因子 =====
FEATURE_COLS = [
    "mom_5","mom_10","mom_20","mom_60","mom_120",
    "turnover_rate","turnover_rate_f","volume_ratio","vol_chg_20",
    "bias_5","bias_10","bias_20","bias_60",
    "macd_dif","macd_dea","macd","kdj_k","kdj_d","kdj_j",
    "rsi_6","rsi_12","rsi_24","cci","boll_pct","boll_width",
    "pe","pe_ttm","pb","ps","ps_ttm","dv_ttm",
    "net_mf_ratio","lg_buy_ratio",
]

# ===== 路径（Studio）=====
WORK_DIR    = "/Users/ziruzhu/stock-tools/_engine_v2_run"
PANEL_PATH  = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股/features_panel.pkl"
DB_PATH     = "/Users/ziruzhu/stock-tools/_weekday_4d_run/stock_mini.db"
XISHEN_PATH = "/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S013-喜神池LightGBM选股/xishen_plus_pool.csv"
VENV_PY     = "/Users/ziruzhu/stock-tools.old.20260725_204255/.venv/bin/python3"
