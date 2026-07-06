#!/bin/bash
# ================================================================
# 股票离线分析系统 — 一键安装脚本
# 在 Mac 终端中运行: bash ~/stock-tools/setup.sh
# ================================================================

set -e

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║   📊 股票离线分析系统 — 安装向导                  ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# 1. 安装 Python 依赖
echo "[1/3] 安装 Python 依赖..."
pip3 install --user akshare pandas numpy 2>&1 | grep -i "success\|already\|error" || true
echo "  ✅ Python 依赖安装完成"
echo ""

# 2. 创建数据目录
echo "[2/3] 创建数据目录..."
mkdir -p ~/stock-data/reports
echo "  ✅ 数据目录: ~/stock-data/"
echo ""

# 3. 下载历史数据
echo "[3/3] 开始下载 A 股历史数据（从 2024年1月 起）..."
echo "  ⏱  预计耗时 30-50 分钟，请耐心等待..."
echo "  💡 可以按 Ctrl+C 中断，下次运行会自动续传"
echo ""

python3 ~/stock-tools/download.py --from 20240101 --delay 0.1

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║   ✅ 安装完成！                                  ║"
echo "║                                                  ║"
echo "║   📊 分析单只股票：                              ║"
echo "║     python3 ~/stock-tools/analyze.py 600519      ║"
echo "║                                                  ║"
echo "║   🔍 扫描超卖股票：                              ║"
echo "║     python3 ~/stock-tools/analyze.py --scan       ║"
echo "║                                                  ║"
echo "║   📈 涨幅榜前20：                                ║"
echo "║     python3 ~/stock-tools/analyze.py --top20      ║"
echo "║                                                  ║"
echo "║   🔄 更新最新数据：                              ║"
echo "║     python3 ~/stock-tools/download.py --update    ║"
echo "╚══════════════════════════════════════════════════╝"
