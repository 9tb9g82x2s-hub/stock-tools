#!/bin/bash
# 双向同步工具 — 泓锦全权托管，老大一句话触发
# 用法：
#   sync studio-to-air   → 从Studio拽关键文件到Air
#   sync air-to-studio   → 把Air新文件推到Studio
#   sync all             → 双向全量同步

STUDIO="studio"  # SSH别名，指向192.168.3.21
AIR_STOCK="$HOME/stock-tools"
AIR_DATA="$HOME/stock-data"
STUDIO_STOCK="/Users/ziruzhu/stock-tools"
STUDIO_DATA="/Users/ziruzhu/stock-data"

case "$1" in
  studio-to-air)
    echo "=== Studio → Air ==="
    rsync -avz --progress "$STUDIO:$STUDIO_STOCK/strategy-lab/" "$AIR_STOCK/strategy-lab/"
    rsync -avz --progress "$STUDIO:$STUDIO_STOCK/automation/" "$AIR_STOCK/automation/"
    echo "Done."
    ;;
  air-to-studio)
    echo "=== Air → Studio ==="
    rsync -avz --progress "$AIR_STOCK/strategy-lab/" "$STUDIO:$STUDIO_STOCK/strategy-lab/"
    echo "Done."
    ;;
  all)
    $0 studio-to-air
    $0 air-to-studio
    ;;
  *)
    echo "Usage: sync {studio-to-air|air-to-studio|all}"
    exit 1
    ;;
esac
