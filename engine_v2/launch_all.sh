#!/bin/bash
# 统一回测引擎v2 — 一键启动全部策略回测
# 用法: bash launch_all.sh [s023|s019|s017|s024|all]

set -e
ENGINE_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV=/Users/ziruzhu/stock-tools.old.20260725_204255/.venv/bin/python3
WORK_DIR=/Users/ziruzhu/stock-tools/_engine_v2_run

mkdir -p "$WORK_DIR"
ln -sf /Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-16-S009-LightGBM多因子选股/features_panel.pkl "$WORK_DIR/" 2>/dev/null || true
ln -sf /Users/ziruzhu/stock-tools/strategy-lab/sessions/2026-07-19-S013-喜神池LightGBM选股/xishen_plus_pool.csv "$WORK_DIR/" 2>/dev/null || true

TARGET="${1:-all}"

run_strategy() {
    local name="$1"
    echo "$(date) ===== 启动 $name ====="
    $VENV "$ENGINE_DIR/backtest_engine_v2.py" "$name" >> "$WORK_DIR/${name}_run.log" 2>&1
    if [ $? -eq 0 ]; then
        echo "$(date) ✓ $name 完成"
    else
        echo "$(date) ✗ $name 失败，查看 $WORK_DIR/${name}_run.log"
    fi
}

if [ "$TARGET" = "all" ]; then
    # 串行跑（节省内存）：s023 → s019 → s017 → s024
    for s in s023 s019 s017 s024; do
        run_strategy "$s"
    done
    echo "$(date) ===== 全部完成 ====="
else
    run_strategy "$TARGET"
fi
