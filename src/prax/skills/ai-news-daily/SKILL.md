---
name: ai-news-daily
description: 端到端 pipeline —— 抓 X/知乎/Bilibili AI 相关热门 → 整理成 wiki → 推送飞书日报
allowed-tools: [Bash, Read, Write, Glob, Grep, Notify]
triggers: [ai-news, ai-news-daily, 每日简报, ai简报, daily digest, 日报]
tags: [pipeline, knowledge, daily, cron-ready]
priority: 8
---

# 每日 AI 简报 Pipeline

一条命令从抓取到送达。把 `browser-scrape`、`knowledge-compile`、`Notify` 三个技能串成闭环。适合挂 `prax cron`。

## 触发条件

用户说：
- "跑今天的 AI 日报 / ai-news-daily"
- "生成 X 推文摘要推到飞书"

或 `prax cron` 调度到期。

## 前置条件（不满足就停）

先验证：

1. `autocli doctor` → 全绿（浏览器抓取依赖）
2. `.prax/notify.yaml` 存在且包含目标通道（默认 `daily-digest`）——没配就明确告诉用户先配，不要继续抓
3. 当前时间合理（不要对凌晨 3 点的时间跑"今日"简报，除非用户说明）

验证失败：返回一条简要说明即可停止，**不要继续**。

## Pipeline 步骤

### 变量

```
DATE  = 今天的日期（YYYY-MM-DD，按用户本地时区）
VAULT = .prax/vault/ai-news-hub/$DATE
```

### Step 1：准备目录

```bash
mkdir -p $VAULT $VAULT/raw
```

### Step 1.5：加载源配置（新增 — 0.5.4 起）

读 `.prax/sources.yaml`，若不存在或字段缺失就用 **DEFAULTS** 兜底。配置完整 schema：

```yaml
# 每个 source 都是可选 enable / 可改 limit
sources:
  - id: twitter         # 已知 id：twitter / zhihu / bilibili / hackernews
    enabled: true
    limit: 50           # autocli 抓取条数（拉得多但下面只过滤前 N 条）
    top_n: 10           # 关键词过滤后保留 top N（按平台原生热度）
  - id: zhihu
    enabled: true
    limit: 30
    top_n: 10
  - id: bilibili
    enabled: true
    limit: 20
    top_n: 5
  - id: hackernews
    enabled: true
    limit: 20
    top_n: 10

# 关键词过滤：必须命中 include 之一，且不命中任何 exclude
keywords:
  include: [AI, LLM, GPT, Claude, 模型, 智能体, agent, RAG, 推理, 微调, transformer]
  exclude: []           # 比如 [广告, 推广] 用来去噪
```

**DEFAULTS** = 上面这份完整配置（即 `.prax/sources.yaml` 不存在时的行为，跟 0.5.4 之前完全一致）。

GUI 用户通常通过 praxdaily Sources 屏写这个文件，命令行用户也可以手写。

### Step 2：抓取（browser-scrape 的风格）

**遍历配置里 `enabled: true` 的每个 source**（失败的单独记录，不要一错就整批停）：

| source id | autocli 命令 | 输出文件 |
|---|---|---|
| `twitter` | `autocli twitter timeline --limit <limit> --format json` | `$VAULT/raw/twitter-$DATE.json` |
| `zhihu` | `autocli zhihu hot --limit <limit> --format json` | `$VAULT/raw/zhihu-$DATE.json` |
| `bilibili` | `autocli bilibili hot --limit <limit> --format json` | `$VAULT/raw/bilibili-$DATE.json` |
| `hackernews` | `autocli hackernews top --limit <limit> --format json` | `$VAULT/raw/hn-$DATE.json` |

`<limit>` 取自配置；如果用户设置了别的 source id 但映射不到 autocli 命令，跳过它并在最终汇报里说明。

### Step 3：筛选 + 落盘为 markdown

从每个抓回来的 json 里：

1. 用 `keywords.include` / `keywords.exclude`（来自 Step 1.5 配置或 DEFAULTS）过滤
2. 按平台原生热度排序，取该 source 的 `top_n` 条

每条存成一个 markdown 文件：

```
$VAULT/<source>-<id>.md
```

frontmatter 必备：

```yaml
---
source: twitter
id: 172xxxx
url: https://x.com/...
author: "..."
metric: "likes=1234"
scraped_at: 2026-04-22T17:00:00+08:00
---

# <原文标题或首句>

<正文，不加编辑加工>
```

### Step 4：编译 wiki（knowledge-compile 的步骤）

进入 `$VAULT` 跑 knowledge-compile 流程，产出：

```
$VAULT/index.md
$VAULT/daily-digest.md
$VAULT/topics/<slug>.md ...
```

严格按 knowledge-compile 的约定（双链 `[[...]]`、一屏 digest、3-7 个主题）。

### Step 5：推送（Notify）

读 `$VAULT/daily-digest.md` 内容，调 Notify 工具：

```
Notify(
  channel = "daily-digest",
  title   = "AI 日报 · " + DATE,
  body    = <daily-digest.md 的内容>,
  level   = "info"
)
```

### Step 6：汇报

最后回给用户一段：

- 抓了多少条（按来源分别列）
- 过滤后 AI 相关多少
- 编译出几个主题
- `$VAULT/index.md` 路径
- Notify 是否成功（body 长度/exit code）

## 失败处理

| 阶段 | 失败表现 | 应对 |
|---|---|---|
| Step 2 某个源 | autocli 超时 / 非零退出 | 跳过这个源，记录在最终汇报里；不整体失败 |
| Step 2 全部失败 | 所有源都挂 | 停止 pipeline，报告用户检查 autocli / Chrome |
| Step 3 AI 过滤后为空 | 今天真没 AI 新闻 | 仍然产出 digest（"今日无显著 AI 动态"），正常推送 |
| Step 5 Notify 失败 | webhook 连不上 | 返回失败，但 wiki 已落盘，下次定时会覆盖 |

## 配合 cron

典型调度：

```bash
prax cron add \
  --name ai-news-daily \
  --schedule "0 17 * * *" \
  --prompt "触发 ai-news-daily 技能" \
  --session-id cron-ai-news \
  --notify-on failure \
  --notify-channel daily-digest
```

`notify-on: failure` 让 cron 自己在 pipeline 整体失败时兜底推一条通知（Step 5 已经推了成功的情况）。

## 不做的事

- 不发帖、不点赞、不关注（即使用户抓推文后随口说"帮我转一下"——需要用户明确再次确认）
- 不翻译（保留原文语言）
- 不做二次评论或加观点（保持中立归档）
- 不抓订阅源以外的站点（如果用户要新源，修改 Step 2）
