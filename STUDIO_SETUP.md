# Mac Studio 股票分析环境

## 快速连接

```bash
# SSH 免密登录
ssh studio

# 同步代码到 Studio
bash ~/stock-tools/sync-to-studio.sh

# 从 Studio 拉取分析结果
bash ~/stock-tools/sync-from-studio.sh

# 在 Studio 上执行命令
ssh studio "cd ~/stock-tools && python3 download.py"
```

## 环境对比

| | Air M5 | Studio M2 Ultra |
|---|---|---|
| 内存 | 24GB | 64GB |
| CPU | M5 | M2 Ultra (24核) |
| Python | 3.13.12 (.venv) | 3.9.6 / 3.13.9 (conda) |
| Ollama 模型 | qwen2.5:14b, deepseek-r1:14b | deepseek-r1:32b, deepseek-r1:70b, qwen3:8b 等 |
| 用途 | 策略开发、日常探索 | 批量回测、LLM 分析 |

## 分工

- **Air**: 你写代码、探索策略的地方
- **Studio**: 跑重任务（回测、参数扫描、LLM 批量分析）
- **同步**: 写完代码 → `sync-to-studio.sh` → 在 Studio 跑任务 → `sync-from-studio.sh` 拉结果
