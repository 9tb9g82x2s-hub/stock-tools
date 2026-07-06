#!/bin/bash
# 同步代码和数据到 Mac Studio
# 用法: bash sync-to-studio.sh

STUDIO="studio"
SSH_KEY="$HOME/.ssh/id_ed25519_studio"
SSH_OPTS="-i $SSH_KEY -o StrictHostKeyChecking=no"

echo "📊 开始同步到 Mac Studio..."

# 同步代码
echo "→ 同步 stock-tools 代码..."
rsync -avz --progress \
    --exclude '.venv' \
    --exclude '__pycache__' \
    --exclude '.workbuddy' \
    --exclude 'cache/' \
    -e "ssh $SSH_OPTS" \
    ~/stock-tools/ "$STUDIO:~/stock-tools/"

# 同步数据（增量）
echo "→ 同步 stock-data..."
rsync -avz --progress \
    -e "ssh $SSH_OPTS" \
    ~/stock-data/ "$STUDIO:~/stock-data/"

echo "✅ 同步完成！"
