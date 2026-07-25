# Stock-tools 双向同步快速指南

**场景**：Air 和 Studio 都跑策略、都会改代码，需要快速双向同步

**方案**：Git 云端仓库 + 一键同步脚本

---

## 为什么选 Git？

双向同步最怕"冲突覆盖"：

| 方案 | 双向改同一文件时 | 风险 |
|---|---|---|
| rsync 脚本 | 后同步的覆盖前一个 | ⚠️ 悄无声息丢代码 |
| iCloud/坚果云 | 生成"冲突副本" | 一堆 `xxx冲突.py`，容易乱 |
| **Git** | 停下来提示冲突，让你选 | ✅ 不丢代码，你决定 |

对策略代码来说，丢掉调试半天的参数 = 白干。Git 是唯一能保证不丢的方案。

---

## 日常操作：一条命令同步

### Air 上（改完代码后）

```bash
cd ~/stock-tools
bash gsync.sh "优化 S019 参数"
```

或者懒得写说明，直接：
```bash
bash gsync.sh
```
会自动用时间戳作为说明。

### Studio 上（回家后配好仓库，日常同步）

```bash
cd ~/stock-tools
bash gsync.sh
```

**gsync.sh 做了什么**：
1. 先 `git pull` 拉取云端最新代码（避免你改的和对方改的冲突）
2. 自动提交本地改动（`git add . && git commit`）
3. 推送到云端（`git push`）
4. 如果遇到冲突，停下来告诉你怎么处理（绝不覆盖）

---

## 初次配置（一次性）

### Air 上（你现在就可以做）

1. 注册 GitHub 账号 → https://github.com
2. 创建私有仓库：名字 `stock-tools`，选 **Private**
3. 安装 GitHub Desktop → https://desktop.github.com
4. 在 GitHub Desktop 里：
   - Add Existing Repository → `/Users/ziruzhu/stock-tools`
   - Publish repository（勾选 Keep this code private）
5. 推送完成，验证：浏览器打开 `https://github.com/你的用户名/stock-tools`，应该能看到 267 个文件

### Studio 上（回家后，在同一网络下操作）

**我来帮你操刀，回家告诉我，我 SSH 进去执行下面的步骤：**

1. 检查并安装 git（如果没装）
2. 克隆仓库：`git clone https://github.com/你的用户名/stock-tools.git`
3. 配置 git 身份（跟 Air 上一样）
4. 测试 `gsync.sh` 脚本

---

## 日常协作流程

### 场景 1：在 Air 上改完，到 Studio 上继续

**Air:**
```bash
cd ~/stock-tools
bash gsync.sh "Air上改了XXX"
```

**Studio:**（回家后，同网络）
```bash
cd ~/stock-tools
bash gsync.sh  # 会自动拉取 Air 推的改动
```

### 场景 2：Studio 跑完回测，回 Air 看结果

**Studio:**
```bash
cd ~/stock-tools
bash gsync.sh "S009回测完成"
```

**Air:**（外出也能同步）
```bash
cd ~/stock-tools
bash gsync.sh  # 拉取 Studio 的回测结果代码
```

### 场景 3：两台同时改了同一个文件（冲突）

假设你在 Air 上改了 `s019.py` 第 10 行，推送了；然后忘了，去 Studio 上也改了同一行，再推送：

```bash
# Studio 上执行 gsync.sh 时
→ [1/3] 拉取云端最新代码...
⚠️  拉取时发生冲突！请手动解决：
   1. 运行 git status 查看冲突文件
   2. 编辑冲突文件，删除 <<<<<<< ======= >>>>>>> 标记，保留想要的内容
   3. 解决后运行：git add . && git commit
   4. 再次运行 bash gsync.sh
```

打开 `s019.py`，会看到：
```python
<<<<<<< HEAD
# Studio 上的改动
hold_days = 15
=======
# Air 上的改动
hold_days = 20
>>>>>>> origin/main
```

你决定保留哪个（或两个都要），删掉标记，保存，然后：
```bash
git add .
git commit -m "解决冲突：保留hold_days=20"
bash gsync.sh
```

---

## 当前进度

✅ **Air 本地仓库**：已就绪（267 文件，4MB，首次提交完成）  
✅ **gsync.sh 脚本**：已写好，Air 和 Studio 通用  
⏳ **待完成**：  
   1. 注册 GitHub → 建私有仓库 → GitHub Desktop 推送（你操作）  
   2. 回家后 Studio clone 仓库（我操刀）

---

## FAQ

**Q：每次都要敲命令吗？有没有全图形化的？**  
A：Air 上可以用 GitHub Desktop 点按钮（Fetch→Commit→Push）。Studio 通过 SSH 远程操作，没桌面，只能命令行，但 `bash gsync.sh` 已经是最简了。

**Q：gsync.sh 会不会把 .pkl 大文件也推上去？**  
A：不会。`.gitignore` 已经配好，大文件会被自动排除。

**Q：如果忘了同步，两台代码差很远，怎么办？**  
A：Git 会自动合并不冲突的部分。只有同一行被两边改了，才需要你手动选。

**Q：能不能自动同步，不用手动执行 gsync.sh？**  
A：可以，但不建议。自动同步遇到冲突时会卡住，你不在电脑跟前反而更麻烦。手动执行至少知道什么时候同步、什么时候遇到问题。

**Q：rsync 脚本还要留着吗？**  
A：留着。在家同网络时，rsync 同步数据（`~/stock-data/`）比 Git 快。代码用 Git，数据用 rsync，各司其职。

---

**下一步**：去 GitHub 完成注册和推送，推完告诉我。回家后我帮你把 Studio 那边配好。
