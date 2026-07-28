#!/bin/bash
# S023网格多版本串行队列（并行版）：5天版跑完后依次跑3天、2天、1天版
# 每个版本内部N个phase并行跑，用train_grid_parallel.py

WORK_DIR="/Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-26-S023-周度5天换仓"
VENV="/Users/ziruzhu/stock-tools.old.20260725_204255/.venv/bin/python3"

cd "$WORK_DIR" || exit 1

echo "[$(date '+%H:%M:%S')] 队列启动：等待5天版完成..."

# 等待5天版完成
while true; do
  if [ -f "s023_grid_5d_result.json" ] && ! pgrep -f "train_grid_parallel.py 5" > /dev/null; then
    echo "[$(date '+%H:%M:%S')] 5天版已完成，开始队列"
    break
  fi
  sleep 120
done

# 依次跑3天、2天、1天（每个版本内部并行）
for DAYS in 3 2 1; do
  echo "[$(date '+%H:%M:%S')] === 启动${DAYS}天并行版 ==="
  $VENV train_grid_parallel.py $DAYS > grid_${DAYS}d_run.log 2>&1
  if [ $? -eq 0 ]; then
    echo "[$(date '+%H:%M:%S')] ${DAYS}天版完成"
  else
    echo "[$(date '+%H:%M:%S')] ${DAYS}天版失败，查看 grid_${DAYS}d_run.log"
    exit 1
  fi
done

echo "[$(date '+%H:%M:%S')] 全部完成：1/2/3/5天版本"
