# results.json 字段规范

每次策略测试完成后，生成一个 `results.json` 文件，字段如下。  
所有策略必须使用统一的字段名和数据类型，否则 dashboard.html 无法正确对比。

---

## 必填字段

```json
{
  "strategy_name": "策略名称（字符串）",
  "created_date": "2026-07-07",
  "strategy_type": "趋势跟踪 | 均值回归 | 动量策略 | 多因子 | 事件驱动 | 其他",

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
    {
      "code": "000001",
      "name": "平安银行",
      "signal_date": "2026-07-01",
      "expected_return": 0.15
    }
  ],

  "ai_analysis": {
    "model": "qwen2.5:14b",
    "summary": "该策略在震荡市中表现中等，趋势市中胜率明显提高……",
    "confidence": "中"
  },

  "notes": "补充说明（可选）"
}
```

---

## 字段说明

### metrics（核心指标）

| 字段 | 类型 | 说明 | 格式 |
|------|------|------|------|
| total_return | number | 总收益率 | 小数，0.123 = 12.3% |
| annual_return | number | 年化收益率 | 小数 |
| win_rate | number | 胜率 | 小数，0.58 = 58% |
| max_drawdown | number | 最大回撤 | 负数，-0.082 = -8.2% |
| sharpe_ratio | number | 夏普比率 | 正数 |
| total_trades | number | 总交易次数 | 整数 |

### aux_metrics（辅助指标）

| 字段 | 类型 | 说明 |
|------|------|------|
| avg_hold_days | number | 平均持仓天数 |
| profit_loss_ratio | number | 盈亏比（平均盈利/平均亏损） |
| max_consecutive_losses | number | 连续最大亏损次数 |

### stocks（入选股票，数组）

| 字段 | 类型 | 说明 |
|------|------|------|
| code | string | 股票代码，6位数字 |
| name | string | 股票名称 |
| signal_date | string | 信号触发日期 YYYY-MM-DD |
| expected_return | number | 预期收益率（可选）|

### ai_analysis（AI 分析，可选）

| 字段 | 类型 | 说明 |
|------|------|------|
| model | string | 使用的 AI 模型名称 |
| summary | string | 分析结论摘要 |
| confidence | string | 置信度：高 / 中 / 低 |

---

## 注意事项

1. **字段名不要改**——dashboard.html 按这些字段名读取数据
2. **数值用小数不要用百分比字符串**——`0.123` 而不是 `"12.3%"`
3. **新增字段不影响已有功能**——dashboard 只读取它认识的字段，多余的会忽略
4. **日期统一用 `YYYY-MM-DD` 格式**
