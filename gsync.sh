#!/bin/bash
# ========================================
# gsync.sh - 策略代码一键同步脚本
# 用法：
#   bash gsync.sh              # 自动同步：先拉取云端，再提交本地改动并推送
#   bash gsync.sh "提交说明"   # 带自定义说明
# 适用：Air 和 Studio 两台机器通用
# ========================================

set -e

REPO_DIR="$HOME/stock-tools"
cd "$REPO_DIR" || { echo "❌ 找不到 $REPO_DIR"; exit 1; }

# 提交说明：用参数，没给就用时间戳+机器名
HOSTNAME_SHORT=$(hostname -s)
MSG="${1:-自动同步 @$HOSTNAME_SHORT $(date '+%Y-%m-%d %H:%M')}"

echo "📊 [$HOSTNAME_SHORT] 开始同步 stock-tools ..."
echo ""

# 第1步：先拉取云端最新代码（避免冲突）
echo "→ [1/3] 拉取云端最新代码..."
if ! git pull --no-edit origin main 2>&1; then
    echo ""
    echo "⚠️  拉取时发生冲突！请手动解决："
    echo "   1. 运行 git status 查看冲突文件"
    echo "   2. 编辑冲突文件，删除 <<<<<<< ======= >>>>>>> 标记，保留想要的内容"
    echo "   3. 解决后运行：git add . && git commit"
    echo "   4. 再次运行 bash gsync.sh"
    exit 1
fi
echo ""

# 第2步：检查本地是否有改动
if git diff --quiet && git diff --cached --quiet && [ -z "$(git status --porcelain)" ]; then
    echo "✓ [2/3] 本地无改动，无需提交"
    echo ""
    echo "✅ 同步完成（本地已是最新）"
    exit 0
fi

# 显示改了哪些文件
echo "→ [2/3] 检测到本地改动："
git status --short
echo ""

# 提交本地改动
git add .
git commit -m "$MSG"
echo ""

# 第3步：推送到云端
echo "→ [3/3] 推送到云端..."
git push origin main
echo ""

echo "✅ 同步完成！提交说明：$MSG"
