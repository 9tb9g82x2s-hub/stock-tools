#!/bin/bash
# 从 Mac Studio 拉取分析结果
# 用法: bash sync-from-studio.sh

STUDIO="studio"
SSH_KEY="$HOME/.ssh/id_ed25519_studio"
SSH_OPTS="-i $SSH_KEY -o StrictHostKeyChecking=no"

echo "📊 从 Mac Studio 拉取结果..."

# 拉取回测报告和日志
rsync -avz --progress \
    -e "ssh $SSH_OPTS" \
    "$STUDIO:~/stock-tools/reports/" ~/stock-tools/reports/

rsync -avz --progress \
    -e "ssh $SSH_OPTS" \
    "$STUDIO:~/stock-tools/logs/" ~/stock-tools/logs/

echo "✅ 拉取完成！"
