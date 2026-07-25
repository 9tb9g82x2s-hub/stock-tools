# Stock-tools Git 同步完整配置指南

**目标**：让 Air 和 Studio 的策略代码通过 GitHub 云端随时同步

**你已完成**：✅ 本地 Git 仓库初始化、.gitignore 配置、首次提交（267 文件，4MB）

**还需要做**：注册 GitHub → 装 GitHub Desktop → 推送到云端 → Studio 上克隆

---

## 第一步：注册 GitHub 账号（5 分钟）

如果你已经有 GitHub 账号，跳到第二步。

1. 浏览器打开 https://github.com
2. 点右上角 **Sign up**（注册）
3. 填写：
   - 邮箱（建议用常用邮箱，会收验证码）
   - 密码（至少 15 个字符，或 8 个字符 + 数字 + 符号）
   - 用户名（可以用 `ziruzhu` 或其他你喜欢的）
4. 验证邮箱（GitHub 会发邮件，点链接激活）
5. 完成后会跳到你的主页，地址类似 `https://github.com/你的用户名`

---

## 第二步：创建私有仓库（3 分钟）

在 GitHub 网页上：

1. 点右上角 **+** 号 → 选 **New repository**
2. 填写信息：
   - **Repository name**: `stock-tools`（仓库名，建议跟本地目录同名）
   - **Description**: 可选，比如"量化策略代码仓库"
   - **权限**：务必选 **Private**（私有，别人看不到）
   - **Initialize this repository**：三个勾选框都不要勾（本地已经有代码了）
3. 点 **Create repository**
4. 创建完成后，GitHub 会显示一个页面，上面有仓库地址，类似：
   ```
   https://github.com/你的用户名/stock-tools.git
   ```
   **这个地址先复制下来，第三步要用**

---

## 第三步：安装 GitHub Desktop（5 分钟）

### 3.1 下载安装

1. 浏览器打开 https://desktop.github.com
2. 点 **Download for macOS**
3. 下载完成后，打开 `GitHub Desktop.dmg`
4. 拖动 GitHub Desktop 图标到 Applications 文件夹
5. 打开 Launchpad，找到 **GitHub Desktop** 并启动

### 3.2 登录账号

1. 首次启动会弹出欢迎界面
2. 点 **Sign in to GitHub.com**
3. 浏览器会打开授权页面，点 **Authorize desktop**
4. 授权完成后自动跳回 GitHub Desktop

### 3.3 配置身份

GitHub Desktop 会让你确认 Name 和 Email，这是 Git 提交时显示的身份：
- **Name**: `ziruzhu`（或你喜欢的名字）
- **Email**: 填你 GitHub 注册的邮箱
- 点 **Continue**

---

## 第四步：把本地仓库推送到云端（3 分钟）

### 4.1 添加本地仓库

1. 在 GitHub Desktop 主界面，点左上角 **Current Repository** 旁的下拉箭头
2. 点 **Add** → 选 **Add Existing Repository**
3. 点 **Choose...** 按钮，选择 `/Users/ziruzhu/stock-tools`
4. 点 **Add Repository**

现在 GitHub Desktop 应该能看到你本地的 267 个已提交文件。

### 4.2 关联云端仓库

1. 点菜单栏 **Repository** → **Repository settings**
2. 在 **Remote** 一栏，点 **Primary remote repository** 下的下拉框
3. 选择刚才在 GitHub 网页上创建的 `stock-tools` 仓库
   - 如果没看到，手动填：
     - **Remote name**: `origin`
     - **Remote URL**: 就是第二步复制的地址，`https://github.com/你的用户名/stock-tools.git`
4. 点 **Save**

### 4.3 推送代码

1. 回到 GitHub Desktop 主界面
2. 点右上角蓝色按钮 **Publish repository**（第一次是 Publish，以后是 Push）
3. 弹窗确认：
   - 勾选 **Keep this code private**（保持私有）
   - 点 **Publish repository**
4. 等待上传（4MB，几秒到几十秒，取决于网速）
5. 上传完成后，GitHub Desktop 会显示 "Last fetched just now"

---

## 第五步：验证云端仓库（1 分钟）

1. 浏览器打开 `https://github.com/你的用户名/stock-tools`
2. 应该能看到：
   - 267 个文件
   - `.gitignore` 文件
   - 最新提交记录："初始提交：策略代码、文档、配置（排除数据/模型/日志）"
   - 文件列表里有 `strategy-lab/`、各种 `.py` 脚本
3. **确认没有大文件**：点进 `strategy-lab/sessions/` 的任意策略，应该只看到 `.py`、`.md`、`.json`，不应该有 `.pkl`、`.csv` 大文件

✅ 如果以上都对，说明推送成功！

---

## 第六步：在 Studio 上克隆仓库（5 分钟）

现在 Air 上的代码已经在云端了，去 Studio 上把它拉下来。

### 6.1 SSH 登录 Studio

在 Air 的终端执行：
```bash
ssh studio
```

### 6.2 在 Studio 上克隆仓库

登录 Studio 后，执行：
```bash
cd ~
# 如果 stock-tools 已存在，先备份
[ -d stock-tools ] && mv stock-tools stock-tools.backup.$(date +%Y%m%d_%H%M%S)

# 克隆仓库（替换成你自己的用户名）
git clone https://github.com/你的用户名/stock-tools.git

# 进入目录确认
cd stock-tools
ls -lh
```

第一次 clone 时，Git 会要求输入 GitHub 的用户名和密码（或 Personal Access Token）。

**如果需要 token**（最近 GitHub 要求用 token 代替密码）：
1. 浏览器打开 https://github.com/settings/tokens
2. 点 **Generate new token (classic)**
3. 勾选 `repo` 权限（完整仓库访问）
4. 点最下方 **Generate token**
5. 复制生成的 token（只显示一次，记得保存）
6. 在 Studio 的 git clone 提示输入密码时，粘贴这个 token

### 6.3 验证同步成功

在 Studio 上执行：
```bash
cd ~/stock-tools
git log --oneline | head -5
ls strategy-lab/sessions/
```

应该能看到跟 Air 上一样的提交记录和目录结构。

---

## 日常使用流程

### Air 上改完代码后推送

1. 打开 **GitHub Desktop**
2. 左侧会显示你修改了哪些文件（绿色 + 号是新增，红色 - 号是删除）
3. 左下角 **Summary** 填简短说明（比如"优化 S019 策略参数"）
4. 点 **Commit to main**（提交到本地）
5. 点右上角 **Push origin**（推送到云端）

### Studio 上拉取最新代码

SSH 登录 Studio 后：
```bash
cd ~/stock-tools
git pull
```

就能把 Air 推送的最新代码拉下来。

### Studio 上改完代码后推送（如果 Studio 也改了代码）

在 Studio 上：
```bash
cd ~/stock-tools
git add .
git commit -m "在 Studio 上修改了 XXX"
git push
```

然后回到 Air 上，GitHub Desktop 点 **Fetch origin** → **Pull origin** 把 Studio 的改动拉回来。

---

## 常见问题

### Q1: Push 时提示 "authentication failed"
**原因**：密码错误或需要用 token。
**解决**：去 https://github.com/settings/tokens 生成 Personal Access Token，勾选 `repo` 权限，复制后粘贴作为密码。

### Q2: Pull 时提示 "merge conflict"
**原因**：Air 和 Studio 同时改了同一个文件的同一行，Git 不知道该保留哪个。
**解决**：
- 在 GitHub Desktop 里，点冲突文件，会显示两边的改动
- 手动编辑文件，保留想要的版本
- 删掉 Git 插入的 `<<<<<<<`、`=======`、`>>>>>>>` 标记
- 保存后，在 GitHub Desktop 里点 **Commit merge**

### Q3: 不小心把大文件（.pkl/.csv）提交了怎么办
**解决**：
```bash
cd ~/stock-tools
# 从 Git 历史中移除大文件（但保留在工作目录）
git rm --cached 大文件路径
git commit -m "移除误提交的大文件"
git push
```

### Q4: 想回到之前的某个版本
**解决**：在 GitHub Desktop 里，点 **History**，找到想回退的提交，右键 → **Revert this commit**。

---

## 总结

✅ **已完成**：Air 本地仓库配置、首次提交  
🔄 **进行中**：推送到 GitHub 云端、Studio 克隆  
📝 **日常流程**：Air 改代码 → GitHub Desktop 提交推送 → Studio 拉取

有任何问题随时问我。这份指南保存在 `/Users/ziruzhu/stock-tools/GIT_SETUP_GUIDE.md`，可以随时查阅。
