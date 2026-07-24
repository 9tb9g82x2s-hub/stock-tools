#!/bin/bash
# S003 牛市超跌掘金 — 一键回测
# 在 Mac Studio 上运行

set -e

echo "===== S003 牛市超跌掘金 回测 ====="
echo ""

# 检查 Python/pandas
if ! python3 -c "import pandas, numpy, scipy, sqlite3" 2>/dev/null; then
    echo "❌ 缺少依赖，正在安装..."
    pip3 install pandas numpy scipy
fi

# 检查数据库
DB="$HOME/stock-data/stock_all.db"
if [ ! -f "$DB" ]; then
    echo "❌ 找不到数据库: $DB"
    echo "   请确认 stock_all.db 在 ~/stock-data/ 下"
    exit 1
fi

DB_SIZE=$(ls -lh "$DB" | awk '{print $5}')
echo "✅ 数据库: $DB ($DB_SIZE)"
echo ""

# 运行回测
echo "🚀 开始回测..."
cd "$(dirname "$0")"
python3 s003_backtest.py

echo ""
echo "===== 回测完成 ====="
