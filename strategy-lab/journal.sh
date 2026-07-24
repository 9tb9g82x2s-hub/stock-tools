#!/bin/bash
# ============================================
# 策略实验室 - 新建操作日记
# 用法: ./journal.sh                    (今天)
#       ./journal.sh 2026-07-08         (指定日期)
# ============================================

set -e

LAB_DIR="$(cd "$(dirname "$0")" && pwd)"
JOURNAL_DIR="$LAB_DIR/journal"

# 解析日期
if [ -z "$1" ]; then
    DATE=$(date +%Y-%m-%d)
else
    DATE="$1"
fi

YEAR_MONTH=$(echo "$DATE" | cut -d'-' -f1-2)
DAY=$(echo "$DATE" | cut -d'-' -f3)

MONTH_DIR="$JOURNAL_DIR/$YEAR_MONTH"
JOURNAL_FILE="$MONTH_DIR/$DAY.md"

# 创建月份目录
mkdir -p "$MONTH_DIR"

# 检查是否已存在
if [ -f "$JOURNAL_FILE" ]; then
    echo "📝 日记已存在: journal/$YEAR_MONTH/$DAY.md"
    echo "   直接编辑即可，或用编辑器打开。"
    exit 0
fi

# 复制模板并替换日期
cp "$JOURNAL_DIR/template.md" "$JOURNAL_FILE"

if [[ "$OSTYPE" == "darwin"* ]]; then
    sed -i '' "s/YYYY-MM-DD/${DATE}/g" "$JOURNAL_FILE"
else
    sed -i "s/YYYY-MM-DD/${DATE}/g" "$JOURNAL_FILE"
fi

echo ""
echo "✅ 操作日记已创建！"
echo ""
echo "📁 文件: journal/$YEAR_MONTH/$DAY.md"
echo "📝 用任意编辑器打开填写即可"
echo "🤖 填完后告诉泓锦'帮我分析今天的日记'，我会给出反馈"
echo ""
