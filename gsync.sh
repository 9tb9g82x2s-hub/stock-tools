#!/bin/bash
# ========================================
# gsync.sh - 策略代码一键同步脚本
# 用法：
#   bash gsync.sh              # 自动同步：先拉取云端，再提交本地改动并推送
#   bash gsync.sh "提交说明"   # 带自定义说明
# 适用：Air 和 Studio 两台机器通用
# ========================================

REPO_DIR="$HOME/stock-tools"
cd "$REPO_DIR" || { echo "❌ 找不到 $REPO_DIR"; exit 1; }

# 确保用 HTTP/1.1，规避 macOS 上偶发的 HTTP2 framing 错误
git config --global http.version HTTP/1.1 2>/dev/null

HOSTNAME_SHORT=$(hostname -s)
MSG="${1:-自动同步 @$HOSTNAME_SHORT $(date '+%Y-%m-%d %H:%M')}"

echo "📊 [$HOSTNAME_SHORT] 开始同步 stock-tools ..."
echo ""

# ---------- 第1步：拉取云端最新代码（带网络重试）----------
echo "→ [1/3] 拉取云端最新代码..."
PULL_OK=0
PULL_OUTPUT=""
for i in 1 2 3; do
    PULL_OUTPUT=$(git pull --no-edit origin main 2>&1)
    PULL_CODE=$?
    if [ $PULL_CODE -eq 0 ]; then
        PULL_OK=1
        echo "$PULL_OUTPUT"
        break
    fi
    # 判断是不是网络类错误（可重试）
    if echo "$PULL_OUTPUT" | grep -qiE "HTTP2|framing|timed out|Could not resolve|Connection|unable to access|Failed to connect|RPC failed"; then
        echo "   ⚠️ 网络波动（第 $i 次），2秒后重试..."
        sleep 2
        continue
    else
        # 非网络错误 → 大概率是真冲突
        break
    fi
done

if [ $PULL_OK -eq 0 ]; then
    echo "$PULL_OUTPUT"
    echo ""
    # 区分：网络问题 vs 真冲突
    if echo "$PULL_OUTPUT" | grep -qiE "HTTP2|framing|timed out|Could not resolve|Connection|unable to access|Failed to connect|RPC failed"; then
        echo "❌ 网络连接失败（重试3次仍不行）"
        echo "   可能原因：网络不稳定 / 需要连VPN / GitHub暂时访问不了"
        echo "   建议：检查网络后重新运行同步"
    elif echo "$PULL_OUTPUT" | grep -qiE "conflict|CONFLICT|Merge conflict"; then
        echo "⚠️  发生代码冲突！两台机器改了同一处，需手动解决："
        echo "   1. 运行 git status 查看冲突文件"
        echo "   2. 编辑冲突文件，删除 <<<<<<< ======= >>>>>>> 标记，保留想要的内容"
        echo "   3. 解决后运行：git add . && git commit"
        echo "   4. 再次运行同步"
    else
        echo "❌ 拉取失败，请把上面的错误信息发给泓锦排查"
    fi
    exit 1
fi
echo ""

# ---------- 第2步：检查本地是否有改动或积压的commit ----------
UNCOMMITTED=$(git status --porcelain)
UNPUSHED=$(git log origin/main..HEAD --oneline 2>/dev/null)

if [ -z "$UNCOMMITTED" ] && [ -z "$UNPUSHED" ]; then
    echo "✓ [2/3] 本地无改动、无积压提交，已是最新"
    echo ""
    echo "✅ 同步完成（本地已是最新）"
    exit 0
fi

if [ -n "$UNCOMMITTED" ]; then
    echo "→ [2/3] 检测到本地改动："
    git status --short
    echo ""
    git add .
    git commit -m "$MSG"
    echo ""
else
    echo "✓ [2/3] 工作区无改动，但有积压的本地提交需要推送"
    echo "$UNPUSHED"
    echo ""
fi

# ---------- 第3步：推送到云端（带网络重试）----------
echo "→ [3/3] 推送到云端..."
PUSH_OK=0
for i in 1 2 3; do
    if git push origin main 2>&1; then
        PUSH_OK=1
        break
    fi
    echo "   ⚠️ 推送失败（第 $i 次），2秒后重试..."
    sleep 2
done

if [ $PUSH_OK -eq 0 ]; then
    echo ""
    echo "❌ 推送失败（重试3次仍不行）。本地已提交，改动没丢。"
    echo "   等网络恢复后再次运行同步即可把本地提交推上去。"
    exit 1
fi
echo ""

echo "✅ 同步完成！提交说明：$MSG"
