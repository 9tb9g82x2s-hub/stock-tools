# 策略实验室

> 投资策略集中管理 · 对比 · 决策

## 这是什么

一个帮你管理所有投资策略测试的结构化项目。每次策略测试的产物（策略逻辑、回测结果、AI 分析）都归档到统一位置，用仪表板快速对比。

## 目录结构

```
strategy-lab/
├── sessions/              ← 所有策略会话
│   └── YYYY-MM-DD-策略名/
│       ├── strategy.md    ← 策略定义
│       ├── results.json   ← 结构化结果
│       ├── report.html    ← 可视化报告
│       └── notes.md       ← 决策笔记
├── templates/             ← 模板
├── dashboard.html         ← 对比仪表板（核心）
├── view.sh                ← 一键打开仪表板
├── new-session.sh         ← 一键创建新策略
└── README.md              ← 本文件
```

## 快速开始

### 1. 创建新策略

```bash
cd ~/stock-tools/strategy-lab
./new-session.sh "你的策略名称"
```

这会在 `sessions/` 下自动创建一个带日期的目录，并复制策略模板。

### 2. 填写策略

编辑 `sessions/YYYY-MM-DD-策略名/strategy.md`，描述策略逻辑和参数。

### 3. 测试和分析

在 WorkBuddy 里告诉我："帮我测试 XXX 策略"，我会：
- 运行回测/扫描
- 把结果写入 `results.json`
- 生成 `report.html`（单策略可视化报告）

### 4. 查看对比

```bash
cd ~/stock-tools/strategy-lab
./view.sh
```

或直接双击 `view.sh`。浏览器会自动打开仪表板，展示所有历史策略的对比。

## 仪表板功能

| 视图 | 功能 |
|------|------|
| 📋 对比表格 | 所有策略核心指标并排对比，点击列头排序，最优值自动高亮 |
| 🎯 雷达图 | 勾选2-5个策略，多维度可视化对比 |
| 📅 时间线 | 按时间看策略迭代方向 |
| 🔗 股票交集 | 发现被多个策略同时选中的股票 |

## results.json 格式

回测结果统一用这个格式。详细字段说明见 `templates/results-schema.md`。

```json
{
  "strategy_name": "策略名称",
  "created_date": "2026-07-07",
  "strategy_type": "趋势跟踪",
  "metrics": {
    "total_return": 0.123,
    "annual_return": 0.085,
    "win_rate": 0.58,
    "max_drawdown": -0.082,
    "sharpe_ratio": 1.45,
    "total_trades": 23
  },
  "aux_metrics": {
    "avg_hold_days": 14,
    "profit_loss_ratio": 2.3,
    "max_consecutive_losses": 4
  },
  "stocks": [
    { "code": "000001", "name": "平安银行", "signal_date": "2026-07-01" }
  ],
  "ai_analysis": {
    "model": "qwen2.5:14b",
    "summary": "该策略在震荡市中表现中等...",
    "confidence": "中"
  }
}
```

## 注意事项

- **文件名不要改**：`strategy.md`、`results.json`、`report.html`、`notes.md` 是标准文件名，仪表板按这些名字读取
- **JSON 格式要严格**：`results.json` 的字段名和类型不要变，否则仪表板无法正确对比
- **策略目录名格式**：`YYYY-MM-DD-策略简称`，这样时间线才能正确排序
- **仪表板需 HTTP 方式打开**：直接用浏览器打开 `dashboard.html` 可能无法加载数据，请使用 `./view.sh`
