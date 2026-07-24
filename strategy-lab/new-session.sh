#!/bin/bash
# ============================================
# 策略实验室 - 新建策略会话
# 用法: ./new-session.sh "策略名称"
# ============================================

set -e

LAB_DIR="$(cd "$(dirname "$0")" && pwd)"
TEMPLATES_DIR="$LAB_DIR/templates"
SESSIONS_DIR="$LAB_DIR/sessions"

# 检查参数
if [ -z "$1" ]; then
    echo "❌ 请提供策略名称"
    echo ""
    echo "用法: ./new-session.sh \"均线交叉策略\""
    exit 1
fi

STRATEGY_NAME="$1"
DATE_PREFIX=$(date +%Y-%m-%d)
DIR_NAME="${DATE_PREFIX}-${STRATEGY_NAME}"
SESSION_DIR="$SESSIONS_DIR/$DIR_NAME"

# 检查是否已存在
if [ -d "$SESSION_DIR" ]; then
    echo "❌ 目录已存在: $DIR_NAME"
    exit 1
fi

# 创建目录
mkdir -p "$SESSION_DIR"

# 复制模板
cp "$TEMPLATES_DIR/strategy-template.md" "$SESSION_DIR/strategy.md"

# 替换策略名称和日期
if [[ "$OSTYPE" == "darwin"* ]]; then
    sed -i '' "s/# 策略名称/# ${STRATEGY_NAME}/" "$SESSION_DIR/strategy.md"
    sed -i '' "s/> 创建日期：YYYY-MM-DD/> 创建日期：${DATE_PREFIX}/" "$SESSION_DIR/strategy.md"
else
    sed -i "s/# 策略名称/# ${STRATEGY_NAME}/" "$SESSION_DIR/strategy.md"
    sed -i "s/> 创建日期：YYYY-MM-DD/> 创建日期：${DATE_PREFIX}/" "$SESSION_DIR/strategy.md"
fi

# 创建空的 results.json 骨架
cat > "$SESSION_DIR/results.json" << EOF
{
  "strategy_name": "${STRATEGY_NAME}",
  "created_date": "${DATE_PREFIX}",
  "strategy_type": "待填写",
  "metrics": {},
  "aux_metrics": {},
  "stocks": [],
  "ai_analysis": {},
  "notes": ""
}
EOF

# 创建空的 notes.md
echo "# ${STRATEGY_NAME} - 决策笔记" > "$SESSION_DIR/notes.md"
echo "" >> "$SESSION_DIR/notes.md"
echo "> 创建时间：${DATE_PREFIX}" >> "$SESSION_DIR/notes.md"
echo "" >> "$SESSION_DIR/notes.md"
echo "## 决策记录" >> "$SESSION_DIR/notes.md"
echo "" >> "$SESSION_DIR/notes.md"

echo ""
echo "✅ 策略会话已创建！"
echo ""
echo "📁 目录: sessions/${DIR_NAME}/"
echo "📝 下一步: 编辑 strategy.md 填写策略逻辑"
echo "📊 分析完成后我会把结果写入 results.json"
echo "🌐 打开 dashboard.html 查看全局对比"
echo ""
