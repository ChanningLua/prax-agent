# 5 分钟入门 · 从 0 到跑出第一个 Prax 命令

**面向**：第一次听说 Prax，还没装过、没配过 API key 的人。

**承诺**：跟着这一页走到底，你会得到：
1. 装好 `prax` 命令
2. 配好至少一个可用的 AI 模型
3. 跑出你的第一个 `prax prompt`

走完之后任何一篇 [tutorials/](./tutorials/) 你都能接着做。

---

## Step 1：装 Prax

需要你机器上有 **Node.js 14+** 和 **Python 3.10+**。

```bash
npm install -g praxagent
```

> Prax 当前只通过 npm 发布。源码安装（`git clone` + `pip install -e .`）见 [CONTRIBUTING.md](../CONTRIBUTING.md)。PyPI 包暂未发布。

### 验证装好了

```bash
prax --version
```

**应该看到**：

```
prax 0.5.0
```

**看到 `command not found`？**

| 场景 | 修复 |
|---|---|
| macOS 用 Homebrew 装的 Node | `export PATH=/opt/homebrew/bin:$PATH` 加到 `~/.zshrc` |
| Linux | 确认 `npm prefix -g` 的 `bin/` 目录在 `$PATH` 上 |
| Windows | 本版暂不官方支持，建议装在 WSL2 里 |

---

## Step 2：让 Prax 知道用哪个 AI 模型

Prax 自己不带 AI 模型。你需要三选一（推荐 **选项 1** 先试）。

### 选项 1：智谱 GLM（国内免费额度最大，新人首选）

1. 打开 <https://open.bigmodel.cn/>，注册
2. 点右上角 **API Keys** → **创建新的 API Key**，复制
3. 在终端跑：

   ```bash
   export ZHIPU_API_KEY="你刚复制的 key"
   ```

4. 想长期生效？把这行加到 `~/.zshrc`（macOS / Linux 默认 shell）或 `~/.bashrc`

**GLM 的 Free plan 够你试完所有教程。** 正式商用建议升级 paid。

### 选项 2：Anthropic Claude（海外用户首选）

1. 打开 <https://console.anthropic.com>，注册
2. 点 **API Keys** → **Create Key**，复制
3. 终端跑：

   ```bash
   export ANTHROPIC_API_KEY="你的 key"
   ```

Claude 是 Prax 原生支持最好的模型家族，质量最高。但海外使用可能需要信用卡。

### 选项 3：OpenAI GPT

1. 打开 <https://platform.openai.com/api-keys>
2. **Create new secret key**，复制
3. 终端跑：

   ```bash
   export OPENAI_API_KEY="你的 key"
   ```

### 验证 key 配对了

```bash
prax providers
```

**应该看到**（例子，你实际看到的取决于配置和 key）：

```
zhipu:
  - glm-4-flash (low, available, ...)
  - glm-4 (standard, available, ...)
anthropic:
  - claude-sonnet-4-7 (premium, missing-credentials, ...)
openai:
  - gpt-5.4 (standard, missing-credentials, ...)
```

只要你用的那家 provider 下至少有一条 `available`，就可以进下一步。

**全是 `missing-credentials`？** 回头检查 Step 2 的 `export` 有没有跑成功。`echo $ZHIPU_API_KEY` 应该回显出你的 key。

---

## Step 3：跑你的第一个 prompt

新建一个空目录（Prax 会在当前目录里建 `.prax/` 存状态）：

```bash
mkdir -p ~/Desktop/prax-hello
cd ~/Desktop/prax-hello
```

跑你的第一条命令：

```bash
prax prompt "你是谁？用一句话回答。"
```

**应该看到**：终端里流出来一段 AI 的回复，类似：

```
我是 Prax 这个智能体运行时里跑的 AI 助手，可以帮你执行代码、测试和自动化任务。
```

**恭喜，你成功了。**

### 常见失败

| 错误信息 | 原因 | 解法 |
|---|---|---|
| `Error: Model '<x>' not found in configuration` | 你配的 key 不是默认模型对应的 provider | 把 `--model` 写清楚：`prax prompt "你是谁" --model glm-4-flash`（如果你配的是 GLM） |
| 卡住 10 秒没反应 | API 连接慢 | 耐心等一下；或 Ctrl+C 重试 |
| `HTTP 401 / Unauthorized` | key 贴错或被重置 | 回 Step 2，重新生成并 export |
| `HTTP 429 / rate limit` | 免费额度用完 | 等 1 分钟，或升级 plan |
| 输出是空行然后就停了 | 模型对接层出了问题 | `prax providers` 看 provider 是否 `available` |

---

## Step 4：跑一条能读文件的 prompt

上一步只是"AI 说话"，现在试试 Prax 最大的能力：**它能操作你本地的文件**。

```bash
echo "hello world" > greeting.txt
prax prompt "读 greeting.txt 里的内容，然后用大写再写回去"
```

**应该看到**：终端显示 Prax 调用 `Read` / `Edit` / `Write` 等工具，最后结束。

**查看结果**：

```bash
cat greeting.txt
```

**应该看到**：

```
HELLO WORLD
```

**这就是 Prax 和其他 AI 聊天框的区别**——它真的能读写文件、跑命令、操作你的工作区。

### 没成功？

| 症状 | 解法 |
|---|---|
| Prax 问"是否允许操作文件" | 输入 `y` 确认；或用 `--permission-mode workspace-write` 提前授权 |
| 文件没被改 | AI 可能理解错了需求。换个更明确的 prompt："把 greeting.txt 的内容改成全大写" |

---

## Step 5：你现在可以干嘛了

把上面 4 步跑通，你就算 Prax onboarding 完成。下一步按你的场景选教程：

| 你想做什么 | 看哪个教程 |
|---|---|
| 处理每天的客服工单 | [`tutorials/support-digest.md`](./tutorials/support-digest.md) |
| 每天抓 X / 知乎 / Bilibili AI 动态，做个知识库 | [`tutorials/ai-news-daily.md`](./tutorials/ai-news-daily.md)（做完你就有 Hermes 同款工作流了） |
| 自动生成发版说明 / CHANGELOG | [`tutorials/release-notes.md`](./tutorials/release-notes.md) |
| 每天 triage 团队 PR | [`tutorials/pr-triage.md`](./tutorials/pr-triage.md) |
| 检测代码-文档 drift | [`tutorials/docs-audit.md`](./tutorials/docs-audit.md) |

有问题：<https://github.com/ChanningLua/prax-agent/issues>

---

## 进阶：把 key 存成持久变量

上面 `export` 的 key 一关终端就没了。长期用请：

### macOS / Linux

```bash
# 如果你用 zsh（macOS 默认）
echo 'export ZHIPU_API_KEY="你的 key"' >> ~/.zshrc
source ~/.zshrc

# 如果你用 bash
echo 'export ZHIPU_API_KEY="你的 key"' >> ~/.bashrc
source ~/.bashrc
```

### 或者用 Prax 自己的 `.prax/.env`

在任何项目目录下建 `.prax/.env`：

```
ZHIPU_API_KEY=你的 key
ANTHROPIC_API_KEY=你的 key
FEISHU_WEBHOOK_URL=你的 webhook
```

Prax 启动会自动加载。这个文件**千万不要 commit 到 git**（跟 `.env` 一样是机密）。

---

## 为什么要装 Prax 而不是直接用 Claude Desktop / Cursor / Hermes

- **Prax 能长跑后台任务**：装个 cron，每天自动跑（Claude Desktop 不行）
- **Prax 能 verify**：改了代码能真跑测试再停（Cursor 可以，但流程是手动的）
- **Prax 本地优先**：敏感数据不一定要走云（[support-digest](./tutorials/support-digest.md) 零外部 API）
- **Prax 开源免费**：runtime 在你本机，不被 vendor 绑定

这些场景 Prax 赢在 runtime 能力；Cursor 赢在编辑器集成；Claude Desktop 赢在易用性。看你主要做啥。
