#!/bin/bash
# Studio批量股价过滤回测 S019（高频10日）

cd ~/stock-tools/strategy-lab/sessions/2026-07-19-S014-Top精选集中策略
LOG=batch_s019.log
echo "=== $(date) 开始批量回测 S019 ===" | tee -a $LOG

for cap in 500 400 300 200; do
    echo "[$(date +%H:%M:%S)] 跑 S019 cap=$cap..." | tee -a $LOG
    ~/stock-tools/.venv/bin/python pf_single_s019.py $cap 2>&1 | tee -a $LOG
    echo "" | tee -a $LOG
done

echo "=== $(date) 完成 ===" | tee -a $LOG
echo "结果: $(ls -lah pricefilter_s019_result.json)" | tee -a $LOG
