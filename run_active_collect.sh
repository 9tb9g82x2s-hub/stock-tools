#!/bin/bash
export PATH=$HOME/.local/bin:$PATH
cd $HOME/stock-tools
mkdir -p logs

echo "[$(date)] 启动活跃池采集" | tee -a logs/active_collect.log
python3 news_collector_v3.py --pool active --notice-only 2>&1 | tee -a logs/active_collect.log
echo "[$(date)] 采集完成" | tee -a logs/active_collect.log
