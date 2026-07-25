#!/bin/bash
# Studio一键安装+运行脚本
# 使用: bash install_and_run.sh

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "======================================"
echo "方案2 Studio环境安装 + 启动"
echo "======================================"

# 1. 确定Python路径
PY="/Users/ziruzhu/.workbuddy/binaries/python/versions/3.13.12/bin/python3"
VENV="/Users/ziruzhu/.workbuddy/binaries/python/envs/default"
if [ ! -d "$VENV" ]; then
    echo "[1/3] 创建虚拟环境..."
    $PY -m venv $VENV
fi
PIP="$VENV/bin/pip"
PYTHON="$VENV/bin/python3"

# 2. 安装依赖
echo "[2/3] 安装依赖..."
$PIP install -q lightgbm scikit-learn optuna pandas numpy
echo "  依赖安装完成"

# 3. 启动训练
echo "[3/3] 启动全量训练..."
cd "$SCRIPT_DIR"
$PYTHON main.py 2>&1 | tee run_log_$(date +%Y%m%d_%H%M%S).txt
echo "======================================"
echo "完成! 查看 candidates.csv 获取候选股"
echo "======================================"
