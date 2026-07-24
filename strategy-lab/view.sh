#!/bin/bash
# ============================================
# 策略实验室 - 打开仪表板
# 启动本地 HTTP 服务器，自动在浏览器中打开仪表板
# 双击此文件或运行: ./view.sh
# ============================================

LAB_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT=8765

echo ""
echo "📊 策略实验室仪表板"
echo "━━━━━━━━━━━━━━━━━━━━"
echo ""

# 检查端口是否被占用，如果被占用就换一个
while lsof -i :$PORT > /dev/null 2>&1; do
    PORT=$((PORT + 1))
done

echo "🔗 启动本地服务器..."
echo "🌐 浏览器将自动打开 http://localhost:$PORT"
echo ""
echo "📁 数据目录: $LAB_DIR/sessions/"
echo "⏹  关闭此窗口或按 Ctrl+C 停止服务器"
echo ""

# 启动 Python HTTP 服务器
cd "$LAB_DIR"
python3 -m http.server $PORT &
SERVER_PID=$!

# 等服务器就绪
sleep 1

# 打开浏览器
open "http://localhost:$PORT/dashboard.html"

# 等待用户关闭
wait $SERVER_PID
