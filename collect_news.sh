#!/bin/bash
# 舆情数据采集 - 快速启动脚本
# 用法：./collect_news.sh [选项]

SCRIPT_DIR="$HOME/stock-tools"
PYTHON="/Users/ziruzhu/.workbuddy/binaries/python/versions/3.13.12/bin/python3"
COLLECTOR="$SCRIPT_DIR/news_collector.py"
LOG_DIR="$SCRIPT_DIR/logs"

# 创建日志目录
mkdir -p "$LOG_DIR"

# 日志文件
TODAY=$(date +%Y%m%d)
LOG_FILE="$LOG_DIR/news_collect_$TODAY.log"

# 默认参数
POOL="tech"
LIMIT=20

# 参数解析
while [[ $# -gt 0 ]]; do
    case $1 in
        --pool)
            POOL="$2"
            shift 2
            ;;
        --limit)
            LIMIT="$2"
            shift 2
            ;;
        --stock)
            STOCK="$2"
            shift 2
            ;;
        --help)
            echo "用法: $0 [选项]"
            echo ""
            echo "选项:"
            echo "  --pool POOL     股票池：tech / my-watch (默认: tech)"
            echo "  --limit NUM     每只股票采集数量 (默认: 20)"
            echo "  --stock CODE    采集单只股票"
            echo "  --help          显示帮助"
            echo ""
            echo "示例:"
            echo "  $0                          # 采集科技股池，每只20条"
            echo "  $0 --pool my-watch --limit 30"
            echo "  $0 --stock 300750.SZ"
            exit 0
            ;;
        *)
            echo "未知参数: $1"
            echo "使用 --help 查看帮助"
            exit 1
            ;;
    esac
done

# 执行采集
echo "========================================" | tee -a "$LOG_FILE"
echo "舆情数据采集 - $(date '+%Y-%m-%d %H:%M:%S')" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

if [ -n "$STOCK" ]; then
    echo "采集股票: $STOCK" | tee -a "$LOG_FILE"
    "$PYTHON" "$COLLECTOR" --stock "$STOCK" --limit "$LIMIT" 2>&1 | tee -a "$LOG_FILE"
else
    echo "采集股票池: $POOL (limit=$LIMIT)" | tee -a "$LOG_FILE"
    "$PYTHON" "$COLLECTOR" --pool "$POOL" --limit "$LIMIT" 2>&1 | tee -a "$LOG_FILE"
fi

# 统计结果
echo "" | tee -a "$LOG_FILE"
echo "========================================" | tee -a "$LOG_FILE"
echo "数据库统计:" | tee -a "$LOG_FILE"
sqlite3 ~/stock-data/stock_all.db "SELECT type, COUNT(*) as count FROM news GROUP BY type;" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"
echo "日志文件: $LOG_FILE"
