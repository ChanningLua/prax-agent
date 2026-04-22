# 傻瓜式教程 · 客服工单简报

**面向**：完全没用过 Prax 的人。只要你能打开终端、会复制粘贴命令就行。

**你是谁**：假设你是一家 30 人 SaaS 公司的 PM。昨晚 deploy 了新版本，今早你想知道：有没有新投诉？`billing` 的抱怨是不是变多了？昨天 200 条 ticket 你不想一条一条看。

**这个教程让你做到的事**：10 分钟内，一条命令生成一份一屏能看完的昨日客服简报。数据不出本机，你老板/合规可以放心。

---

## 做之前你需要：

- 一台装了 Prax 的 macOS / Linux 电脑
- 会打开终端
- 能复制粘贴

**不需要**：会写代码 / 懂 git / 会配 Chrome / 有 GPT Plus。

### 检查 Prax 装好了没

打开终端，粘贴这行：

```bash
prax --version
```

**应该看到**：

```
prax 0.3.2
```

（0.3.2 或更高都行。）

**如果看到 `command not found`**：请先跟 [Getting Started](../getting-started.md) 走一遍再回来。

---

## Step 1：挑个放东西的目录

找个空目录放这个教程的演示文件。你可以：

```bash
mkdir -p ~/Desktop/prax-support-demo
cd ~/Desktop/prax-support-demo
```

**你会发现**：从现在起所有命令都在这个目录下跑。

---

## Step 2：放一份示例工单数据进来

Prax 自带了 8 条假工单数据给你练手，位置在 Prax 装好的仓库里。

先找到 Prax 装在哪：

```bash
python3 -c "import prax, os; print(os.path.dirname(prax.__file__))"
```

**会打印**类似：

```
/opt/homebrew/lib/node_modules/praxagent
```

把这个路径记下来，我们叫它 `$PRAX_DIR`。下面复制示例数据：

```bash
# 先在当前目录建 Prax 的标准 inbox 目录
mkdir -p .prax/inbox

# 从 Prax 装的目录把示例 json 拷过来
cp $(python3 -c "import prax, os; print(os.path.dirname(prax.__file__))")/docs/recipes/support-digest/sample-tickets.json \
   .prax/inbox/tickets-2026-04-21.json
```

**确认文件到位了**：

```bash
ls -la .prax/inbox/
```

**应该看到**：

```
-rw-r--r--  ... tickets-2026-04-21.json
```

**打开看一眼数据长啥样**（可选）：

```bash
head -30 .prax/inbox/tickets-2026-04-21.json
```

你会看到 8 条虚构工单：billing 重复扣费、登录故障、iOS 闪退等等。

---

## Step 3：跑一次简报生成

粘贴这条命令（注意是一行，别断开）：

```bash
prax prompt "生成 2026-04-21 的客服简报，数据在 .prax/inbox/tickets-2026-04-21.json"
```

**会看到**：终端滚很多行文字，Prax 在分析、分类、脱敏。持续 30 秒到 2 分钟（取决于你配的模型和网络）。

**看到类似这样的结束语就是成功了**：

```
客服简报已生成：
- digest：.prax/vault/support/2026-04-21/digest.md
- redacted data：.prax/vault/support/2026-04-21/tickets-redacted.json
- 原始文件归档至：.prax/inbox/archive/tickets-2026-04-21.json
```

### 常见失败

| 错误信息 | 解法 |
|---|---|
| `Error: Model '...' not found` | Prax 的 API key 没配好。去 `prax doctor` 看哪个模型可用；或 `export GLM_API_KEY=...` 配个国产模型先用 |
| 卡住 >3 分钟没反应 | Ctrl+C 停掉，重跑。大概率是第一次联 API 慢或网络抖动 |
| `No such file or directory: tickets-...` | 回到 Step 2 确认文件在 `.prax/inbox/` 下 |

---

## Step 4：看简报

```bash
cat .prax/vault/support/2026-04-21/digest.md
```

**会看到**类似（每次文字略有差异，但结构相同）：

```markdown
---
date: 2026-04-21
ticket_count: 8
category_count: 5
highlights_count: 5
---

# 客服简报 · 2026-04-21

## 总览（一句话）
昨日共 8 条工单，billing 类占主导（4 条，50%），其中 2 条 high severity。

## 今日亮点（top 5）

### 1. [high] Billing — duplicate charge
- 工单数：2
- 代表性摘要："On April 20th I was charged twice for the Pro plan..."
- 建议 owner：@finance-ops

### 2. [escalated] Billing — refund overdue, 律师话术
- 工单数：1
- 代表性摘要："Ticket T-10201 was supposed to be refunded... Considering contacting my lawyer..."
- 建议 owner：@finance-lead （升级）

...
```

**这就是你要的东西**：

- ✅ **5 条 highlights**：不是 200 条，是最需要关注的 5 条
- ✅ **PII 已脱敏**：邮箱 `jane.doe@example.com` 变成 `j***@e***.com`，手机号中间 4 位变成 `****`
- ✅ **有建议**：哪个 owner 接，哪条该升级

---

## Step 5：看脱敏后的 raw 数据（验证合规）

PM 用 digest，但**合规官**可能想看"脱敏确实做了"。

```bash
cat .prax/vault/support/2026-04-21/tickets-redacted.json | head -50
```

**你会发现**：email / phone / 身份证号都被替换成了遮罩形式。

```json
{
  "id": "T-10342",
  "customer_email": "j***@e***.com",
  "body": "On April 20th I was charged twice... My card ends in 4241..."
}
```

这个文件可以安全地进 BI、周报、仪表盘。

**原始带 PII 的文件被移到了**：

```
.prax/inbox/archive/tickets-2026-04-21.json
```

这是你的唯一备份，原位置已经不在了。

---

## Step 6：换成你真的工单数据

演示跑通了，换成你自己的。

### 从 Zendesk / Freshdesk / Intercom 导出

每家工具都有 export 功能。导出成 JSON 或 CSV。最小字段要求：

```json
[
  {
    "id": "T-xxxx",                      // 必需
    "created_at": "2026-04-21T14:22:00+08:00",  // 必需
    "body": "工单正文",                   // 必需
    "severity": "high",                  // 可选但强烈建议
    "category": "billing",               // 可选但强烈建议
    "customer_email": "...",             // 脱敏自动处理
    "status": "open",
    "subject": "短标题"
  }
]
```

### 放到 inbox 目录

```bash
# 假设你今天导出的是 2026-04-22 的工单
cp ~/Downloads/my-tickets-export.json .prax/inbox/tickets-2026-04-22.json
```

### 跑简报

```bash
prax prompt "生成 2026-04-22 的客服简报"
```

跟 Step 3 一模一样。

---

## Step 7（可选）：每天自动跑

这步开始就要"每天自动化"了。你需要：

**准备 A**：每天有人（或脚本）往 `.prax/inbox/` 丢 `tickets-<日期>.json`
**准备 B**：确认 `prax cron` 能跑（macOS/Linux 支持；Windows 暂不支持）

```bash
prax cron add \
  --name support-digest-daily \
  --schedule "0 9 * * *" \
  --prompt "生成昨日客服简报（最新的 tickets-*.json 文件）" \
  --session-id cron-support-digest
```

**应该看到**：

```
Added cron job 'support-digest-daily'
```

装调度器：

```bash
prax cron install
```

**应该看到**：

```
plist_path: /Users/你/Library/LaunchAgents/dev.prax.cron.dispatcher.plist
label: dev.prax.cron.dispatcher
...
```

明天早上 9:00 就会自动跑。

**查任务列表**：

```bash
prax cron list
```

**想删**：

```bash
prax cron remove --name support-digest-daily
```

---

## Step 8（可选）：简报直接发到飞书群

这步开始要**飞书机器人 webhook**。如果你们公司用 Slack / 邮件，流程类似，但这里先讲飞书。

### A. 拿飞书 webhook（30 秒）

1. 打开飞书，进一个群（比如 `pm-客服简报` 群）
2. 点**右上角设置（齿轮）** → **群机器人** → **添加机器人**
3. 选 **自定义机器人**
4. 名字随意（例如 "Prax 客服简报"），**复制** 它给你的 webhook URL，形如：
   ```
   https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxxxxxxxxxx
   ```

### B. 告诉 Prax 这个 URL

把 webhook 存成环境变量：

```bash
export FEISHU_PM_WEBHOOK="https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxxxxxxxxxx"
```

（想让它下次开终端还在？把这行加到你的 `~/.zshrc` 或 `~/.bashrc`。）

写 `.prax/notify.yaml`：

```bash
cat > .prax/notify.yaml <<'YAML'
channels:
  pm-team:
    provider: feishu_webhook
    url: "${FEISHU_PM_WEBHOOK}"
    default_title_prefix: "[客服简报] "
YAML
```

### C. 测一条消息

```bash
prax prompt "调用 Notify 工具给 pm-team channel 发一条 title=测试 body=Hello"
```

**飞书群里应该瞬间出现一张蓝色卡片**，标题 `[客服简报] 测试`，内容 `Hello`。

**没收到？**

| 问题 | 解法 |
|---|---|
| Prax 报 "channel not found" | `.prax/notify.yaml` 写错地方了。确认在当前目录 `.prax/` 下 |
| Prax 说发了但飞书没收到 | 检查 `echo $FEISHU_PM_WEBHOOK` 是不是完整 URL，不要有换行 |
| 收到但中文乱码 | 飞书 webhook 不支持字符集问题。检查你的终端 locale（`echo $LANG` 应 `zh_CN.UTF-8` 或 `en_US.UTF-8`） |

### D. 让 cron 跑完自动推

回到 Step 7 改 cron 任务，加 notify 字段：

```bash
prax cron remove --name support-digest-daily
prax cron add \
  --name support-digest-daily \
  --schedule "0 9 * * *" \
  --prompt "生成昨日客服简报（最新的 tickets-*.json 文件），完成后调用 Notify 工具把 digest 内容发到 pm-team channel" \
  --session-id cron-support-digest \
  --notify-on failure \
  --notify-channel pm-team
```

现在每天早上 9:00 简报会自动到你飞书群。`--notify-on failure` 表示只有跑出错才单独再发一条报警。

---

## 排错总表

| 症状 | 位置 | 解法 |
|---|---|---|
| `prax: command not found` | Step 0 | 参见 [getting-started](../getting-started.md) |
| `Model '...' not found` | Step 3 | `prax doctor` 看哪个模型有 key；按提示配 |
| 简报内容和示例差很多 | Step 4 | Prax 用的模型越弱，输出越模糊。试 `prax prompt ... --model claude-sonnet-4-6` |
| 脱敏没生效 | Step 5 | 你的数据字段名不标准，Prax 没认出 `customer_email` / `phone` 是 PII。在 `.prax/support-digest.yaml` 配 `redaction.extra_regex` |
| cron 装了不触发 | Step 7 | `launchctl list \| grep dev.prax`；没条目就重 `prax cron uninstall && prax cron install` |
| Notify 说"channel not found" | Step 8 | `cat .prax/notify.yaml` 确认 `pm-team` channel 在里面；注意缩进必须是 2 个空格 |

---

## 你现在可以干嘛

走通整个流程之后，你实际拥有了：

1. **每天 9:00 自动生成的一屏客服简报**，推到飞书
2. **脱敏后的结构化数据**，可以直接进 BI / 给合规看
3. **本地 only 的处理**，敏感数据不出本机

下一步想玩点别的：

- 想生成**发版说明**？看 [`release-notes`](../recipes/release-notes.md)
- 想让 Prax **triage 你们的 PR**？看 [`pr-triage`](../recipes/pr-triage.md)
- 想抓 X 推文做 AI 日报？看 [ai-news-daily tutorial](./ai-news-daily.md)

遇到问题：文件 issue 到 <https://github.com/ChanningLua/prax-agent/issues>。
