#!/bin/bash
# 12档位并行启动（24核全用上）
cd ~/stock-tools/strategy-lab/sessions/2026-07-19-S014-Top精选集中策略
rm -f pricefilter_s*.json all_done.flag

echo "=== $(date) 并行启动12档位 ===" | tee parallel_start.log

for s in s017 s018; do
  for cap in 500 400 300 200; do
    nohup ~/stock-tools/.venv/bin/python pf_single.py $s $cap > pf_${s}_${cap}.log 2>&1 &
    echo "启动 $s cap=$cap PID=$!" | tee -a parallel_start.log
  done
done

for cap in 500 400 300 200; do
  nohup ~/stock-tools/.venv/bin/python pf_single_s019.py $cap > pf_s019_${cap}.log 2>&1 &
  echo "启动 s019 cap=$cap PID=$!" | tee -a parallel_start.log
done

sleep 3
echo "--- 运行中进程数 ---" | tee -a parallel_start.log
ps aux | grep pf_single | grep -v grep | wc -l | xargs echo "进程数:" | tee -a parallel_start.log
