#!/bin/bash
# 5天版止损扫描：-3/-5/-7/-10/-12/-15 + 无止损(0)
SESS="/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-21-S017-喜神池20天月度策略"
PY="/Users/ziruzhu/stock-tools/.venv/bin/python"
BATCHLOG="$SESS/batch_stoploss.log"
cd /Users/ziruzhu/stock-tools
echo "[$(date '+%H:%M:%S')] 止损扫描开始" > "$BATCHLOG"

# -12% 已有结果(s013_long_h5_d5_result.json,无sl后缀),跳过重跑
for SL in -0.03 -0.05 -0.07 -0.10 -0.15 0; do
  echo "[$(date '+%H:%M:%S')] 开始 STOP_LOSS=$SL" >> "$BATCHLOG"
  "$PY" "$SESS/train_backtest_s017.py" 5 1 s013 0 5 $SL >> "$BATCHLOG" 2>&1
  echo "[$(date '+%H:%M:%S')] 完成 STOP_LOSS=$SL (exit=$?)" >> "$BATCHLOG"
done
echo "[$(date '+%H:%M:%S')] 全部完成" >> "$BATCHLOG"
