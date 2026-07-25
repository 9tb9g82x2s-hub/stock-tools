# 方案2 - 翻倍起点预测模型（Studio全量版）

## 一句话说明
用LightGBM学习"个股翻倍启动点前30天的特征"，预测哪些股票正处于翻倍起点。
方案1（Air）已验证AUC=0.73有效，本方案在Studio上做全量+超参搜索+丰富特征。

## 回家后怎么跑（3步）

```bash
# 1. 把整个 plan2_studio 文件夹同步到 Studio
# 2. 确认 config.py 里 DB_PATH 指向 Studio 上的 stock_all.db 路径
# 3. 一键运行：
cd plan2_studio
bash install_and_run.sh
```

就这样。脚本会自动装依赖、跑全流程、输出结果。全量预计30-90分钟（取决于Studio核数）。

## 关键参数（已在Air调参锁定，不要改）
- `LOOKBACK = 30`：特征窗口=起点前30天（调过15/20/30/45/60/90，不敏感，30最优）
- `FWD_MIN=10, FWD_MAX=60`：起点后10-60天内翻倍（调过多种，综合最优）
- `DOUBLE_THR = 2.0`：翻倍定义=最高价≥起点收盘价×2

## Studio专属配置（充分利用算力，不迁就Air）
- `N_WORKERS`：自动=CPU核数-2，多进程并行特征工程
- `OPTUNA_TRIALS = 100`：超参搜索100轮（Air只用固定参数）
- `CV_FOLDS = 5`：时间序列5折交叉验证
- `LGB_ROUNDS = 3000`：最大树数，early stopping自动控制

## 文件结构
```
plan2_studio/
├── config.py           # 所有配置（改DB_PATH在这）
├── features.py         # 特征工程（45个特征：动量/波动/均线/量能/技术指标/资金流/龙虎榜/估值）
├── main.py             # 主流程（7个step）
├── evaluate.py         # 分层回测+IC+特征重要性
├── scan_market.py      # 扫描当前市场输出候选股
├── install_and_run.sh  # 一键安装+运行
├── smoke_test.py       # 冒烟测试（Air上已验证通过）
└── README.md
```

## 产出物
- `plan2_model.txt`：训练好的模型
- `plan2_samples.csv`：全量样本
- `evaluation_report.txt`：评估报告（分层回测/多阈值/特征重要性）
- `feature_importance.csv`：特征重要性
- `candidates.csv`：当前市场候选股TOP50（★评级）
- `candidates_top200.csv`：候选股TOP200

## 防未来函数说明（重要）
1. 特征只用起点前30天数据（起点当天都不碰）
2. 起点定位+样本标签只用END_DATE(2026-06-30)之前的数据，避开7月大跌
3. 数据加载到7月24日仅用于最后选股扫描，不参与训练
4. 时间切分：2016-2023训练，2024-2026.6测试（非随机切分）

## Air冒烟测试结果（300只票，供参考）
- 全流程7个step跑通，无bug
- 最高分档翻倍率80% vs 基准36% = 提升2.21倍
- 分层回测呈现明显梯度（高分档翻倍率显著更高）
- 全量版预计效果更好（样本更多、超参搜索、特征更丰富）

## 注意事项
- SCORE_DATE=20260724 是当前扫描日，如需扫描其他日期改config
- 市值过滤：5-1000亿（剔除超小盘垃圾股和超大盘）
- 已剔除北交所
