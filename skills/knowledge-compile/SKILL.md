---
name: knowledge-compile
description: 把一堆 raw markdown（抓取/笔记/文章）压成 Obsidian 风格 wiki —— 有 TOC、有按主题聚合、有日简报
allowed-tools: [Read, Write, Glob, Grep, Bash]
triggers: [整理, 编译, compile, wiki, 知识库, digest, 简报, 归档, 沉淀, llm-wiki, 日报, 周报, obsidian]
tags: [knowledge, compile, wiki, obsidian, digest]
priority: 7
---

# 知识编译技能：把 raw markdown 变 wiki

把一个目录下的散装 markdown（抓取的推文、知乎回答、文章笔记 ...）整理成结构化 wiki。产出在 Obsidian 里双链可用。

## 何时用

- 用户说"整理今天抓的内容"、"把 X 目录编译成 wiki"、"生成日报/周报"
- 和 `browser-scrape` 技能配套：先抓后编
- 和 `cron` 任务配套：定时 pipeline 的最后一环

## 输入

一个目录，里面可能有：

- 多个 `*.md` 文件（来自不同来源：twitter、zhihu、bilibili、read...）
- 可选的 `raw/*.json`（原始抓取数据，用作溯源）

## 产出（约定）

写回**同一个目录**下：

```
<input-dir>/
├── index.md              # TOC + 一览表
├── daily-digest.md       # 一屏内看完：一句话总览 + 100 字摘要 + 今日亮点
├── topics/
│   ├── <topic-slug>.md   # 按主题聚合，内部用 Obsidian 双链 [[source-file]] 回引原文
│   └── ...
└── raw/                  # 保留原始文件（若已有）
```

**硬约束**：
- 只新增 / 覆盖上面四类文件；**不要删** `.md` 原文或 `raw/`
- 原文保留在原路径，通过 `[[文件名]]` 双链引用
- 所有时间用 ISO 格式（`2026-04-22T17:05:00+08:00`）
- 中文优先，技术术语保留英文原词

## 标准执行流程

### 步骤 1：扫描输入目录

```
Glob: <input-dir>/*.md
Glob: <input-dir>/raw/*.json   # 可选
```

读每个 `.md`，抽取 frontmatter（至少 `source / url / scraped_at` 可能存在），取第一段作为摘要候选。

### 步骤 2：聚类成主题

- 用内容相似性 + 关键词共现，把文件分成 3-7 个主题
- 主题名短、名词化、中文：如 `模型发布`、`AI 工具`、`工程实践`、`安全与对齐`
- 每篇文章归入一个**主主题**（避免重复计数带来的膨胀）
- 主题 slug 用拼音或英文短语：`model-releases`、`ai-tools`、`engineering`、`safety-alignment`

### 步骤 3：写 `topics/<slug>.md`

模板：

```markdown
---
topic: 模型发布
slug: model-releases
article_count: 5
generated_at: 2026-04-22T17:05:00+08:00
---

# 模型发布

## 本期概要
<2-3 句话说明本主题下的整体动态>

## 条目

### [[twitter-17xxx]] — Anthropic 发布 Claude Opus 4.7
- 来源：X @AnthropicAI
- 时间：2026-04-22 09:30
- 要点：
  - <bullet 1>
  - <bullet 2>

### [[zhihu-abc123]] — <标题>
...
```

**关键点**：
- `[[文件名]]` 不带 `.md` 后缀，指向原文件 stem
- 一条一个 H3，不要堆成长段落
- 要点用 bullet，每条 ≤ 30 字，严禁复述原文

### 步骤 4：写 `index.md`

```markdown
---
generated_at: 2026-04-22T17:05:00+08:00
article_count: 23
topic_count: 5
---

# 索引 · 2026-04-22

## 主题一览

| 主题 | 条目数 | 入口 |
|---|---|---|
| 模型发布 | 5 | [[topics/model-releases]] |
| AI 工具 | 8 | [[topics/ai-tools]] |
...

## 原始文件

- [[twitter-17xxx]]
- [[zhihu-abc123]]
- ...（按时间倒序）
```

### 步骤 5：写 `daily-digest.md`

**硬约束**：**一屏能看完**。用户在微信日报里看到的就是这份。

```markdown
---
date: 2026-04-22
article_count: 23
topic_count: 5
---

# 今日简报 · 2026-04-22

## 一句话总览
<15 字以内的主线，例如：Anthropic 发 Opus 4.7，社区讨论集中在长上下文成本>

## 100 字摘要
<精炼到 3-4 句话>

## 今日亮点
1. **模型发布**：Opus 4.7 上线，上下文 1M tokens（[[topics/model-releases]]）
2. **AI 工具**：Cursor 推出背景 Agent（[[topics/ai-tools]]）
3. **工程实践**：...

## 报告位置
- 完整索引：[[index]]
- 原始归档：`<input-dir>`
```

### 步骤 6：返回摘要给用户

简要告诉用户：
- 扫描到多少文件、聚成多少主题
- 产出的 `index.md` / `daily-digest.md` / `topics/*` 路径
- 如果后续要推送（`Notify` 工具 / `cron` 的 `notify_channel`），提示用户要把 `daily-digest.md` 的内容作为通知 body

## 和其他技能的接力

| 上游 | 本技能 | 下游 |
|---|---|---|
| `browser-scrape` 抓到 `<dir>/*.md` | 编译成 wiki | `Notify` 工具把 `daily-digest.md` 发到飞书 |
| 用户手动往目录里丢 markdown | 同上 | 同上 |
| `cron` 定时触发 | 输入目录由 `date` 决定 | cron 的 `notify_on=success` 自动触发 |

## 常见错误与对策

- **聚类过细**：20 篇文章分出 15 个主题 → 主题变"标签"。合并成 3-7 个主主题
- **主题间重复**：同一篇在多个主题里出现 → 每文只归一个主主题
- **digest 太长**：超过一屏就不叫 digest。限制：总览 15 字、摘要 100 字、亮点最多 5 条
- **忘了双链**：用 `[原文](file.md)` 写法 Obsidian 不能双向跳转；必须用 `[[file]]`
- **误删原文**：本技能只写不删，原文件保持不动

## 不做的事

- 不做事实核验（Fact-check 不是本技能职责）
- 不做翻译（除非用户明确要求）
- 不加广告/情绪评价（保持中立）
- 不抓新内容（抓取是 `browser-scrape` 的事）
