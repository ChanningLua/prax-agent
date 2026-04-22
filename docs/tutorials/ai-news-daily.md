# 傻瓜式教程 · AI 日报自动化 Pipeline

**面向**：想复刻 Hermes 文章里那套"自动抓推文 → 自动整理 → 自动推日报"的人。不会写代码也能做到。

**你是谁**：你是产品经理 / 运营 / AI 爱好者。每天想看 X、知乎、Bilibili 上和 AI 相关的热门内容，但自己翻花太多时间。

**完成后你会拥有**：每天下午 5 点自动跑、推文整理成 Obsidian wiki、简报自动发到飞书/邮箱。一次配好永远不用管。

---

## 为什么分 4 个阶段

这个 pipeline 有 4 个零件：

1. **整理 (compile)** —— AI 把散文件变 wiki（最简单）
2. **抓取 (scrape)** —— 去 X / 知乎 / Bilibili 抓内容（要装 AutoCLI + Chrome 扩展）
3. **定时 (cron)** —— 每天自动跑（要装 LaunchAgent）
4. **推送 (notify)** —— 推到飞书群（要拿 webhook）

**每个阶段独立可用**。如果你只想"先看看整理效果，再决定要不要折腾抓取"，就只做 Phase 1。想躺平玩自动化就往后做。

---

## 前置

- 已经完成 [Getting Started](../getting-started.md)（装 Prax + 配 API key + 跑通 `prax prompt`）
- `prax --version` 能打出 0.3.2 或更高

---

## Phase 1 · 用假数据跑"整理"看看（5 分钟，零外部依赖）

这一步让你**不装任何东西**就能看到 Prax 把散文件整理成 wiki 的效果。

### Step 1：准备目录

```bash
mkdir -p ~/Desktop/prax-ai-news
cd ~/Desktop/prax-ai-news
```

### Step 2：把教程自带的 6 条样本内容拷过来

这 6 条是模拟"昨天抓到的"推文/知乎/HN/Bilibili，我已经替你准备好了：

```bash
TODAY=$(date +%F)
mkdir -p .prax/vault/ai-news-hub/$TODAY

PRAX_DIR=$(python3 -c "import prax, os; print(os.path.dirname(prax.__file__))")
cp $PRAX_DIR/docs/tutorials/ai-news-daily/sample-vault/*.md \
   .prax/vault/ai-news-hub/$TODAY/
```

**确认到位**：

```bash
ls .prax/vault/ai-news-hub/$TODAY/
```

**应该看到**：

```
bilibili-bv001.md
hn-40321.md
twitter-17242.md
twitter-17243.md
twitter-17244.md
zhihu-abc001.md
```

**打开看一条**（可选）：

```bash
cat .prax/vault/ai-news-hub/$TODAY/twitter-17242.md
```

你会看到一条假推文，frontmatter 有 `source / author / metric / scraped_at`。

### Step 3：让 Prax 整理这个目录

```bash
prax prompt "对 .prax/vault/ai-news-hub/$(date +%F)/ 下的 markdown 跑 knowledge-compile 技能，产出 index.md、topics/ 和 daily-digest.md"
```

**等 30 秒到 2 分钟**。Prax 会在目录下新增几个文件。

**完成后**：

```bash
ls .prax/vault/ai-news-hub/$TODAY/
```

**应该多出**：

```
daily-digest.md      ← 一屏能看完的简报
index.md             ← TOC
topics/              ← 按主题聚合的 wiki 条目
```

### Step 4：看产出

```bash
cat .prax/vault/ai-news-hub/$TODAY/daily-digest.md
```

**应该看到**类似（Prax 每次措辞略有差异）：

```markdown
# 今日简报 · 2026-04-22

## 一句话总览
大模型发版潮：Claude Opus 4.7、GPT-5 reasoning 同日预览

## 100 字摘要
Anthropic 发 Claude Opus 4.7，OpenAI 推 GPT-5 reasoning preview。
Karpathy 提醒长上下文经济性问题。知乎讨论国产 agent 追赶。HN 讨论
无观测性的 agent loop 烧钱隐患。Bilibili 有人教搭自动知识库。

## 今日亮点
1. **模型发布**：Claude Opus 4.7 + GPT-5 reasoning（[[topics/model-releases]]）
2. **工程实践**：长上下文成本 + agent observability（[[topics/engineering]]）
3. **生态观察**：国产 agent 追赶、自动化工作流（[[topics/ecosystem]]）

## 报告位置
- 完整索引：[[index]]
```

再看主题归档：

```bash
ls .prax/vault/ai-news-hub/$TODAY/topics/
cat .prax/vault/ai-news-hub/$TODAY/topics/model-releases.md
```

会看到 `[[twitter-17242]]` 这种双链——把这个目录整个拖进 **Obsidian** 打开，双链自动工作，反向链接、图谱视图都能用。

**Phase 1 到这里结束**。你已经看到整条 pipeline 的终点形态。接着再决定要不要自己装抓取和自动化。

### Phase 1 排错

| 症状 | 解法 |
|---|---|
| `cp: No such file or directory: .../sample-vault/*.md` | Phase 1 Step 2 的 `$PRAX_DIR` 没算对。手跑 `python3 -c "import prax, os; print(os.path.dirname(prax.__file__))"` 看输出，改用绝对路径 |
| Prax 没产出 `topics/` 目录 | 用的模型太弱。换强一点的：在 prompt 末尾加 ` --model claude-sonnet-4-6` |
| digest 太长（超过一屏） | Prax 没遵守约束。重跑，或明确说"daily-digest.md 一屏内" |

---

## Phase 2 · 装 AutoCLI，真去抓 X / 知乎 / Bilibili（20 分钟）

**这一阶段需要**：macOS / Linux + Chrome + 你想抓的站点已登录。

### Step 1：装 AutoCLI 二进制

去 <https://github.com/nashsu/AutoCLI/releases> 下载对应平台的 release。

macOS ARM 示例：

```bash
# 下载（替换成 release 页里的真实 URL）
curl -L https://github.com/nashsu/AutoCLI/releases/latest/download/autocli-darwin-arm64 \
  -o /tmp/autocli
chmod +x /tmp/autocli
mv /tmp/autocli /usr/local/bin/autocli

# 验证
autocli --version
```

**应该看到**：

```
autocli 1.x.x
```

**看到 `command not found`？** `mv` 那步放到了 `/usr/local/bin`，要在 PATH 里。终端跑 `echo $PATH` 确认包含 `/usr/local/bin`。

### Step 2：装 Chrome 扩展

1. 打开 Chrome，地址栏输入 `chrome://extensions/`
2. 打开右上角 **开发者模式**
3. 从 AutoCLI release 里下载扩展 zip，解压
4. 点 **加载已解压的扩展程序**，选刚解压的目录
5. 扩展图标应当出现在浏览器栏

详细步骤参见 <https://github.com/nashsu/AutoCLI#chrome-extension-setup>。

### Step 3：登录你要抓的站点

在 Chrome 里正常登录 X / 知乎 / Bilibili。保持 Chrome 运行着。

### Step 4：诊断

```bash
autocli doctor
```

**应该看到**：

```
✓ Chrome detected
✓ Extension connected
✓ Ready to scrape
```

**任一条是 ✗**：按提示修复，通常是 Chrome 没开、或扩展没启用、或权限没给。别跳过！后面抓不到东西全是这里没 OK。

### Step 5：抓一次真数据

```bash
# 先备份 Phase 1 的样本目录
mv .prax/vault/ai-news-hub/$(date +%F) .prax/vault/ai-news-hub/$(date +%F)-sample

# 新建今日目录
mkdir -p .prax/vault/ai-news-hub/$(date +%F)/raw
```

让 Prax 调 AutoCLI 抓 X：

```bash
prax prompt "触发 browser-scrape 技能：用 autocli 抓 X 最近 20 条推文，存成 markdown 到 .prax/vault/ai-news-hub/$(date +%F)/，每条一个文件，raw json 同时存到 raw/ 子目录"
```

**等 30 秒到 2 分钟**。Prax 会先 `autocli doctor`，再 `autocli twitter timeline --limit 20 --format json`，然后写文件。

### Step 6：验证

```bash
ls .prax/vault/ai-news-hub/$(date +%F)/
```

**应该看到**有 10+ 个 `twitter-<id>.md` 和一个 `raw/` 子目录。

**随便打开一个**：

```bash
cat .prax/vault/ai-news-hub/$(date +%F)/twitter-*.md | head -30
```

应当是真实推文（你登录账户能看到的时间线）。

### Step 7：整理成 wiki（重跑 Phase 1 Step 3）

```bash
prax prompt "对 .prax/vault/ai-news-hub/$(date +%F)/ 下的 markdown 跑 knowledge-compile"
```

出来的 `daily-digest.md` 这次是基于**真实数据**的了。

### Phase 2 排错

| 症状 | 解法 |
|---|---|
| `autocli: command not found` | Step 1 的二进制没放 PATH 里。`which autocli` 空的话重放一次 |
| `autocli doctor` 里 `Extension not connected` | Chrome 没开 / 扩展被禁 / Chrome 被"推荐"关 tab 清掉了。打开 Chrome，确认扩展图标存在 |
| 抓不到推文（timeline 返回空） | 当前 Chrome 账户没登录 X。先在 Chrome 里 x.com 登录一次 |
| 抓到 5 条就停了 | X 风控。隔 30 分钟再试；长期需要降低抓取频率 |

---

## Phase 3 · 每天自动跑（10 分钟）

### Step 1：加 cron 任务

```bash
prax cron add \
  --name ai-news-daily \
  --schedule "0 17 * * *" \
  --prompt "触发 ai-news-daily 技能，抓今日 X 推文 top 10，整理成 wiki，存到 .prax/vault/ai-news-hub/$(date +%F)/" \
  --session-id cron-ai-news
```

**应该看到**：

```
Added cron job 'ai-news-daily'
```

### Step 2：装调度器

```bash
prax cron install
```

**应该看到**：

```
plist_path: /Users/<你>/Library/LaunchAgents/dev.prax.cron.dispatcher.plist
...
```

### Step 3：查是否挂上

```bash
launchctl list | grep dev.prax
```

**应该看到**：

```
-       0       dev.prax.cron.dispatcher
```

### Step 4：看一眼任务列表

```bash
prax cron list
```

**应该看到**：

```
ai-news-daily        0 17 * * *         触发 ai-news-daily 技能...
```

明天下午 5 点自动跑。日志在 `.prax/logs/cron/ai-news-daily-*.log`。

### Step 5（推荐）：马上验证一次

不想等到明天？手动触发一次 dispatcher：

```bash
prax cron run
```

**如果有到期 job 就会执行**。想看 dispatcher 触发过没有：

```bash
ls -la .prax/logs/cron/ 2>/dev/null
```

### Phase 3 排错

| 症状 | 解法 |
|---|---|
| `launchctl list` 没 `dev.prax` | `prax cron install` 时 launchctl load 报错被忽略了。手跑 `launchctl load -w ~/Library/LaunchAgents/dev.prax.cron.dispatcher.plist` 看错误 |
| 日志说 `autocli: command not found` | LaunchAgent PATH 和你的终端不同。`export PRAX_BIN=$(which prax)`、`prax cron uninstall && prax cron install` 重装，会继承当前 PATH |
| 到点没跑 | Mac 睡眠时 LaunchAgent 不跑。用 `caffeinate` 防睡眠，或在 Mac 打开 **系统设置 → 电池 → 防止电脑在显示器关闭时自动睡眠** |

---

## Phase 4 · 推送到飞书群（10 分钟）

### Step 1：拿飞书 webhook

1. 打开飞书，进一个群（例如建个"我的 AI 日报"群，把自己拉进去）
2. **右上角齿轮 → 群机器人 → 添加机器人 → 自定义机器人**
3. 名字叫"Prax AI 日报"
4. **复制** 它给你的 webhook URL，形如：
   ```
   https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxxxxxx
   ```

### Step 2：存成环境变量

```bash
export FEISHU_AI_NEWS="https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxxxxxx"
```

想长期生效？加到 `~/.prax/.env`（如果没有就 `mkdir -p ~/.prax && touch ~/.prax/.env` 建）：

```
FEISHU_AI_NEWS=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxxxxxx
```

### Step 3：写 notify.yaml

```bash
cat > .prax/notify.yaml <<'YAML'
channels:
  ai-news-digest:
    provider: feishu_webhook
    url: "${FEISHU_AI_NEWS}"
    default_title_prefix: "[AI 日报] "
YAML
```

### Step 4：测一条

```bash
prax prompt "调用 Notify 工具给 ai-news-digest 发 title=测试 body=hello"
```

**飞书应当瞬间收到蓝色卡片**。

**没收到？**

| 问题 | 解法 |
|---|---|
| Prax 说 "channel 'ai-news-digest' not found" | `cat .prax/notify.yaml` 确认 yaml 在当前目录 `.prax/` 下、缩进两空格 |
| 发了但飞书没收到 | `echo $FEISHU_AI_NEWS` 确认是完整 URL、末尾没空格没换行 |
| 机器人在群里被移除过 | 重加机器人，重拿 webhook（URL 会变） |

### Step 5：让 cron 完成后自动推

删旧的 job，加上 notify 配置：

```bash
prax cron remove --name ai-news-daily
prax cron add \
  --name ai-news-daily \
  --schedule "0 17 * * *" \
  --prompt "触发 ai-news-daily 技能，抓今日 X 推文 top 10 整理成 wiki，最后调用 Notify 把 daily-digest.md 内容发到 ai-news-digest channel" \
  --session-id cron-ai-news \
  --notify-on failure \
  --notify-channel ai-news-digest
```

从明天开始：

- 每天 17:00 自动抓 → 整理 → 推送
- 成功就飞书群里看到"AI 日报"卡片
- 跑失败 Prax 自己再推一条 `[AI 日报] cron [ai-news-daily] failure` 给你警报

---

## 完成！你现在有的东西

1. 本地 Obsidian wiki（在 `.prax/vault/ai-news-hub/` 下按日期归档，双链可用）
2. 每天 17:00 自动刷新
3. 飞书日报推送（一屏内可见的 AI 热点）
4. 敏感数据全在本机，AutoCLI 用你的 Chrome 登录态，不存任何凭据

**和 Hermes 文章里那套的区别**：Prax 是开源 runtime，不绑定云端，可以本地完全跑。跟你老板汇报时一句话：

> "我们用开源 runtime 实现了 Hermes 同款工作流，数据不出本机，合规可审计。"

---

## 后续

- 想加知乎 / Bilibili 源？改 `skills/ai-news-daily/SKILL.md` 的 Step 2（或在你的 `.prax/skills/` 下覆盖一个同名 skill）
- 想换推送平台？改 `.prax/notify.yaml` 的 provider（支持 `feishu_webhook` / `lark_webhook` / `smtp`）
- 想多一个"科技新闻"、"游戏资讯"同款 pipeline？复制 `skills/ai-news-daily/SKILL.md` 改关键词，再配套新的 cron job

问题：<https://github.com/ChanningLua/prax-agent/issues>
