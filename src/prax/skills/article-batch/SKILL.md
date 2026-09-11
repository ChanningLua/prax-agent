---
name: article-batch
description: 一次规划并推进 2–10 篇需求导向的技术文章，隔离研究、证据和质量状态
allowed-tools: [Bash, Read, Write, Edit, Glob, Grep, WebSearch, WebCrawler]
triggers: [十篇文章, 批量写作, 文章批次, 一天十篇, article batch, batch writing]
tags: [research, writing, batch, trend, demand, longform]
priority: 11
---

# 十篇文章批处理

这个技能负责吞吐，不降低 `hotspot-article` 的证据和质量门。十篇文章使用十个独立工作区，任何一篇失败都不能污染其他文章。

## 目标与边界

- 默认 `count=10`，允许 2–10；
- 默认组合：`event 30% / demand 50% / evergreen 20%`；
- 每篇都要有完整业务场景和七段技术到业务桥；
- 每篇先过题目重量门；小版本、小功能和局部技术点直接降级，不占十篇精选名额；
- 每篇独立维护来源、实测、事实核验和质量报告；
- 不为了凑十篇复制角度、来源或段落；
- 不自动发布。当天未通过的文章保留草稿和失败原因。

## Step 1：检查开源适配器

```bash
python3 -m prax.article_integrations probe --format json
```

按可用性使用：

1. TrendRadar：聚合公开热榜与 RSS，读取 `TRENDRADAR_OUTPUT`；
2. AutoCLI：抓取需要 Chrome 登录态的平台；
3. Crawl4AI：把公开文章转成研究用 Markdown；缺失时使用遵守 `robots.txt` 的 HTTP 回退；
4. GPT Researcher：并行生成研究线索；需要本地包、模型和检索凭据；
5. STORM：读取 `STORM_OUTPUT` 中已有报告，补充视角和追问。

外部研究报告不是事实来源。必须回到其中列出的原始 URL。

## Step 2：建立至少 12 个候选

十篇批次至少准备 12 个候选，避免一个题失败后整批停住。候选写入：

```text
.prax/vault/article-batches/<DATE>/topic-candidates.json
```

候选必须符合 `hotspot-article` 的 `business_case` 和 `business_bridge` schema。只复述发布会或产品功能的题目不得进入批次。

## Step 3：创建十个隔离工作区

```bash
python3 -m prax.article_batch \
  ".prax/vault/article-batches/<DATE>/topic-candidates.json" \
  --count 10 \
  --date "<DATE>" \
  --out-root ".prax/vault/article-batches/<DATE>/run"
```

生成：

```text
run/
├── batch-manifest.json
├── calibration-gate.json
├── 01-<topic>/
│   ├── brief.md
│   ├── business-bridge.json
│   ├── research-plan.json
│   └── status.json
└── 10-<topic>/...
```

`batch-manifest.json` 没有 `complete: true` 时，不开始写作；先补候选。重复运行必须显式使用 `--resume`，避免误覆盖批次状态。

## Step 4：先用一篇做校准门

批次创建后只推进 `calibration-pilot`，其他目录保持 `waiting-for-calibration`。首篇必须完整走到来源、实测、事实审校、去 AI 腔编辑和质量门，再把实际暴露的问题写入 `calibration-gate.json`：

- 机器分在哪些地方虚高或误判；
- 哪些来源只进研究池、没有真正进入正文；
- 计划验证与实际运行之间还差什么；
- 哪些段落虽然过门，读起来仍像模板；
- 题目是否足以改变一个真实业务决策，还是被篇幅硬撑成“大文章”；
- 已经修改了哪些 prompt、规则或 hard gate。

只有首篇 hard gates 通过、编辑七项均至少 4/5、证据明确写出局限，并且上述改动已经落地，才把 `status` 改为 `released`、`remaining_slots_released` 改为 `true`。不能先批量生成十篇再统一返工。

## Step 5：校准后按两条并发泳道推进

不要十篇同时抢浏览器和检索限额。最多同时研究两篇：

1. 泳道 A 研究第 1 篇时，泳道 B 研究第 2 篇；
2. A 进入写作后，再把第 3 篇放入研究；
3. 每完成一个阶段就更新该目录的 `status.json`；
4. 单篇失败只标记 `blocked`，继续下一篇。

建议状态：

`planned → researching → outlined → drafting → fact-checking → quality-gate → ready`

## Step 6：逐篇执行 hotspot-article

在当前 agent 内直接执行 `hotspot-article` 的 Step 3–9，不要递归调用 `prax prompt`。

每篇先读 `brief.md`、`business-bridge.json` 和 `research-plan.json`。可用 Crawl4AI 抓正文：

```bash
python3 -m prax.article_integrations fetch \
  "https://example.com/original" \
  --out "<WORKSPACE>/research/source-01.md"
```

可用 GPT Researcher 扩大问题面：

```bash
python3 -m prax.article_integrations research \
  "<文章主题、决策和研究问题>" \
  --out "<WORKSPACE>/research-leads.md"
```

无论使用哪个后端，仍要完成：

- 按文章路线完成来源和证据：分析稿至少 12 个来源，框架稿至少 8 个，实测稿至少 4 个来源且必须有两次真实运行；
- 至少 4 个第一方来源；
- 一次与业务决策直接相关的验证；
- 至少 8 个事实主张核验；
- `business_bridge`、篇幅、结构和引用 hard gates；
- 独立“去 AI 腔”编辑。

## Step 7：批次验收

完成后更新 `batch-manifest.json` 中每篇状态并输出：

- `ready`、`draft`、`blocked` 各多少篇；
- 每篇机器质量分和未通过门槛；
- 总来源数、重复来源数和第一方来源占比；
- 外部适配器实际使用情况；
- 所有成稿和草稿路径。

十篇中某篇未过门，不得拿另一篇的来源、实测或质量报告代替。
