---
name: hotspot-article
description: 从近期大事件、真实需求和常青决策中选题，完成多源研究、业务落地、实测、事实核验和精选文章
allowed-tools: [Bash, Read, Write, Edit, Glob, Grep, WebSearch, WebCrawler]
triggers: [热点文章, 热点追踪, 业务选题, 用户需求, 深度文章, 长文, 对标精选, hotspot article, longform, editorial pipeline]
tags: [research, writing, hotspot, demand, fact-check, longform]
priority: 10
---

# 热点深度文章 Pipeline

这不是“一次提示词写长文”，而是一条有证据链和退出门槛的编辑流水线：

`三轨机会池 → 需求优先评分 → 技术到业务桥接 → 多源研究 → 多视角提纲 → 实测 → 成稿 → 事实/技术/文风审校 → 质量门`

对标腾讯云“内容精选”时，先读 `docs/recipes/tencent-selected-benchmark.md`，再按内容类型选质量档，不能用一个固定篇幅处理所有文章：

- `selected-analysis`：重大事件或行业变化，6,000–12,000 有效字符；
- `selected-framework`：架构、组织或决策框架，5,500–11,000 有效字符；
- `selected-hands-on`：产品实测或项目实战，4,500–9,000 有效字符。

三类都控制在 5–9 个 H2。重量来自攻击链、分类框架、真实输入输出、失败修复、架构图和明确取舍，不来自拆出更多章节。`selected-sharp` 只用于技术短稿，不计入腾讯云精选批次；`selected` 保留为兼容旧稿。

机器分只衡量可审计的下限，不能把 `8.6/10` 直接解释为审美或洞察得分。最终发布仍需人工主编确认。

## 触发条件

- “追一下今天的 AI 热点，写成深度文章”
- “从客户项目和业务难题里找一个值得写的选题”
- “对标精选文章，做一篇 8–9 分的长文”
- “运行 hotspot-article”

如果用户没有给主题，同时扫描近期大事件、真实需求和常青决策三条线。默认周内容组合为：**时事 30%、需求/解决方案 50%、常青框架 20%**。选题先补齐业务场景，再按当前组合缺口选择；热度很高但说不清“谁在什么情况下要做什么决定”的内容，只进简报，不写精选文章。

把选题与评分写入 `brief.md`，不中断执行等待确认；如果涉及政治、医疗、金融、法律或未成年人等高风险主题，则停在选题报告，等待用户确认。

## 文件约定

```
ROOT=.prax/vault/hotspot-articles/<YYYY-MM-DD>/<topic-slug>

$ROOT/
├── brief.md
├── topic-candidates.json
├── opportunity-report.json
├── business-bridge.json
├── demand-brief.md
├── research-notes.md
├── source-index.json
├── outline.md
├── evidence.json
├── evidence/
├── draft.md
├── fact-check.json
├── fact-check.md
├── editorial-review.md
├── quality-report.json
└── publish-ready.md       # 只有全部门槛通过后才创建
```

新文件使用 Write；修改已有文件使用 Edit。保留中间稿和失败报告，不覆盖研究证据。

## Step 0：读取项目配置

可选配置 `.prax/article.yaml`：

```yaml
audience: 中文开发者与技术决策者
topics: [AI Agent, LLM, RAG, 开源工具]
exclude: [纯融资传闻, 无原始出处的爆料]
lookback_hours: 48
candidate_limit: 20
tone: 克制、清晰、证据优先
target_mix:
  event: 0.30
  demand: 0.50
  evergreen: 0.20
```

没有配置就使用以上默认值。当前日期和时区必须写入 `brief.md`。

## Step 1：构建三轨机会池

每个候选标注一个 `track`：

### A. 时事线 `event`

- 当天 `.prax/vault/ai-news-hub/<DATE>/`；
- 先运行 `python3 -m prax.article_integrations probe`，把可用和缺失的适配器写入 `brief.md`；
- `TRENDRADAR_OUTPUT` 存在时，用 `python3 -m prax.article_integrations ingest-trendradar` 归一化其 Markdown、JSON 或 SQLite 输出；
- AutoCLI 可用时，通过 `collect-autocli` 抓登录态平台；扩展或登录态失败只跳过该源；
- 官方博客、GitHub Trending、Hacker News、行业媒体；
- 正文优先使用 Crawl4AI；未安装时适配器会遵守 `robots.txt` 并退回公开 HTTP 抓取。

### B. 需求线 `demand`

- 客户项目 brief、售前/客服问题和交付复盘（先脱敏）；
- 搜索词、站内搜索无结果、相关问题与持续出现的长尾查询；
- GitHub Issues/Discussions、技术社区中反复出现的“怎么做/怎么选/为什么失败”；
- 招聘 JD、岗位能力变化、培训与管理难题；
- 产品使用数据、试用流失、工单和销售异议；
- 文章评论中请求方案、补充案例或表达自身痛点的内容。

### C. 常青线 `evergreen`

- 架构取舍、成本模型、迁移指南、排障手册；
- 每季度仍会遇到的采购、合规、团队协作和人才培养决策；
- 旧文章中持续带来搜索、收藏、咨询和内部转发的问题。

每个候选保存标题、来源、时间、平台指标和抓取时间。平台不可比的点赞、排名不得直接相加。单张截图或发布一小时内的高评论率只能触发进一步研究，不能直接证明“热门”或“真实需求”；尽量保存 `1h / 6h / 24h` 快照，并把评论分成方案询问、真实案例、反驳讨论和普通互动。

## Step 2：先补业务场景和技术桥，再算机会分

每个候选必须回答：

| 字段 | 要回答的问题 |
|---|---|
| actor | 谁正在遇到问题 |
| job_to_be_done | 他要完成什么任务 |
| trigger | 为什么现在必须处理 |
| current_workaround | 当前怎么凑合解决 |
| decision | 读完文章要做什么选择 |
| cost_of_inaction | 不处理有什么损失 |
| evidence | 客户 brief、工单、搜索、评论、Issue 等需求证据 |

缺少两个以上字段的候选不能进入精选长文。不要编造客户、预算、工单或业务结果。

再建立一条不能跳步的技术到业务链：

`来源信号 → 技术变化 → 受影响流程 → 业务决策 → 验证方法 → 成功指标 → 不适用条件`

| 字段 | 合格标准 |
|---|---|
| source_signal | 写清事件、客户问题或长期信号，并保留原始出处 |
| technical_delta | 说明能力、约束或架构相对之前具体变了什么，不写“效率提升”一类空话 |
| affected_workflow | 落到售前、研发、客服、运营、招聘、审计等具体流程节点 |
| business_decision | 决策人读完后要采用、延期、自建、采购、替换或停止什么 |
| validation_method | 给出可重复的对照、试点、回放、压测或失败注入方法 |
| success_metric | 使用业务方约定的质量、时延、成本、风险或人效指标 |
| no_fit_conditions | 明确哪些组织、数据或流程条件下不值得采用 |

七项必须全部存在。事件再热，只要还停留在产品功能介绍、发布会复述或技术名词解释，就把 `editorial_route` 设为 `daily-digest`，不能进入精选长文。需求或常青题缺桥时设为 `research-brief`，先补现场证据。

再过一道“题目重量门”。至少满足以下三项中的两项：

- 会改变预算、权限、架构、岗位或跨团队流程中的一项；
- 能用一个真实或明确标注的参考业务流程，从触发走到验收和失败处置；
- 决策错误会带来可说明的交付、成本、安全或组织后果。

单个协议字段、局部版本变化、某个仓库去掉一层组件、厂商小功能更新，默认只做技术短帖。除非能证明它已经造成大范围迁移、真实客户损失或重大的采购决策，否则不得靠扩写升格为精选文章。

### 撞题门

在目标社区检查最近 30 天的精选内容。为每个候选记录：

- 已有文章是否覆盖同一事件或需求；
- 中心结论和业务落点是否相同；
- 本文新增的是原创运行证据、未被注意的原始材料、真实客户现场，还是只换了一种说法。

同题已有更完整文章时，候选必须降级或换题。不能因为已经做完研究就继续发布。

把候选写入 `topic-candidates.json`，信号均按 0–5 分：

```json
{
  "portfolio_counts": {"event": 3, "demand": 4, "evergreen": 1},
  "target_mix": {"event": 0.3, "demand": 0.5, "evergreen": 0.2},
  "candidates": [
    {
      "id": "ai-search-build-or-buy",
      "title": "AI 联网搜索应该自建还是采购",
      "track": "demand",
      "signals": {
        "problem_frequency": 4,
        "decision_urgency": 5,
        "scenario_specificity": 5,
        "evidence_strength": 4,
        "audience_fit": 5,
        "attention_velocity": 3,
        "cross_source": 3,
        "depth": 5,
        "content_gap": 4,
        "risk": 1,
        "saturation": 3
      },
      "business_case": {
        "actor": "正在做企业搜索的技术负责人",
        "job_to_be_done": "给客户确定可交付的联网搜索方案",
        "trigger": "项目进入技术选型",
        "current_workaround": "逐个对比搜索 API 和开源框架",
        "decision": "自建、采购或混合",
        "cost_of_inaction": "错过交付期或持续产生错误答案",
        "evidence": ["脱敏客户 brief", "社区重复问题"]
      },
      "business_bridge": {
        "source_signal": "客户项目进入 AI 联网搜索选型",
        "technical_delta": "实时检索增加了新鲜度、引用、延迟和成本约束",
        "affected_workflow": "售前调研、方案架构、上线验收和质量回归",
        "business_decision": "采购搜索 API、开源自建或采用混合架构",
        "validation_method": "用固定查询集比较覆盖率、引用正确性、延迟和成本",
        "success_metric": "达到客户约定的质量、时延和预算阈值",
        "no_fit_conditions": "固定语料且不需要实时信息时不引入联网检索"
      }
    }
  ]
}
```

运行确定性评分器：

```bash
python3 -m prax.topic_opportunity \
  "$ROOT/topic-candidates.json" \
  --json-out "$ROOT/opportunity-report.json" \
  --bridge-out "$ROOT/business-bridge.json"
```

机会分由 **真实需求 45%、注意力 20%、编辑价值 35%** 组成，再扣传闻风险和内容饱和度。候选还必须满足：总分至少 65、证据强度至少 3、风险不高于 2、业务场景完整度至少 0.75、技术到业务桥完整度为 1.00。

评分器优先补齐周内容组合缺口。没有历史时先从 `demand` 线选择；需求线没有合格项才回退到其他轨道。评分结果同时给出 `longform`、`daily-digest`、`research-brief` 或 `hold` 路由。把入选主题扩写为 `demand-brief.md`，并让评分器生成 `business-bridge.json`；后者只复制候选中已经写明的链路，不替作者补造业务事实。纯新闻只能解释“发生了什么”时，进入日报而不是精选长文。

## Step 3：研究，不先写正文

先验证需求本身。`demand` 轨道至少要有两类独立信号，例如“脱敏客户 brief + 重复搜索问题”或“工单 + GitHub Issues”；早期互动数字只能算其中半类。无法补足时，把文章降级为探索稿，不创建 `publish-ready.md`。

再逐段验证 `business-bridge.json`：

- `source_signal` 用原始材料确认，不拿转载标题代替；
- `technical_delta` 用文档、代码、论文或可复现结果说明“变了什么”；
- `affected_workflow` 用客户流程、工单、访谈或公开案例确认影响位置；
- `business_decision` 至少比较两个真实可选方案，不能预设产品必买；
- `validation_method` 和 `success_metric` 必须能在 Step 5 实际执行或观测；
- `no_fit_conditions` 要保留，即使它会缩小文章适用范围。

可选运行 GPT Researcher：

```bash
python3 -m prax.article_integrations research \
  "<主题 + 业务决策 + 六个研究问题>" \
  --out "$ROOT/research-leads.md"
```

它的输出只算研究线索。每个 URL 仍要单独读取并进入 `source-index.json`，不能把聚合报告自身拆成多个独立来源。STORM 已有输出可通过 `STORM_OUTPUT` 作为提纲和追问参考导入，仍不得跳过本文的来源、实测和事实门。

来源数量按路线确定：`selected-analysis` 研究至少 12 条，`selected-framework` 至少 8 条，`selected-hands-on` 至少 4 条。实测路线不能用堆来源代替真实操作；分析路线也不能用一次配置检查代替技术纵深。优先顺序：

1. 官方公告、产品文档、标准、代码仓库；
2. 原始论文、数据集、基准报告；
3. 有署名和编辑流程的专业媒体；
4. 专家分析与社区讨论，仅用于观点或线索。

同一新闻的转载不算独立来源。每条来源必须读到支持主张的具体段落；只看搜索摘要不计入研究数。

`source-index.json` 格式：

```json
{
  "sources": [
    {
      "id": "S01",
      "title": "来源标题",
      "url": "https://example.com/original",
      "source_type": "official",
      "published_at": "2026-07-29",
      "accessed_at": "2026-07-29T16:00:00+08:00",
      "supports": ["关键主张 A"],
      "notes": "原文证据的准确释义"
    }
  ]
}
```

正文引用统一写作 `[S01]`。精确数字、日期、性能结论和直接归因必须紧邻引用。无法核验的精确数字应删除或明确标成估计。

## Step 4：多视角提纲

先读 `demand-brief.md` 和 `business-bridge.json`。开头 300 字内要让读者看到具体角色、触发场景和待做决策；正文至少给出一张决策表、一个真实工作流或案例，以及不适用条件。时事只占背景所需篇幅，主线按“技术变化如何影响流程—有哪些选择—怎样验证”展开。

在 `outline.md` 中让四个角色分别提出问题，再删去与核心决策无关的问题，合并为一条叙事主线：

- 工程师：原理、实现路径、性能和复现条件；
- 产品负责人：用户场景、收益、采用成本；
- 安全/合规审稿人：攻击面、隐私、失败条件；
- 怀疑者：反例、替代解释、营销话术。

所有路线的 H2 控制在 5–9 个。相邻内容能在同一节回答就不拆节，但每个 H2 必须新增事实、机制、案例、产物或决策中的至少一项。

- `selected-analysis`：现场 → 完整事实链 → 被忽略的技术细节 → 机制解释 → 企业影响 → 可执行方案 → 边界；
- `selected-framework`：真实问题 → 分类轴 → 分类矩阵 → 逐类决策 → 跨类场景 → 反例与边界；
- `selected-hands-on`：为什么测试 → 真实输入 → 运行过程 → 失败与修复 → 最终产物 → 瑕疵 → 适用人群。

## Step 5：必须做一次真实验证

验证必须对应 `business-bridge.json` 中的 `validation_method` 和 `success_metric`，不能测了一个方便运行但与业务决策无关的指标。`selected-hands-on` 至少完成两个实际运行或案例；其他路线至少一次。验证可以是：

- 运行开源项目的最小复现；
- 调用公开 API 并保存响应；
- 对公开数据做可重复的计算；
- 对多个官方版本/参数做结构化对照。

不要编造终端结果。无法运行时，把原因写入 `evidence.json` 并停止在草稿状态，不能生成 `publish-ready.md`。

`evidence.json` 格式：

```json
{
  "runs": [
    {
      "name": "最小复现",
      "method": "完整、可重复的方法",
      "command": "实际执行的命令（若适用）",
      "result": "观察到的结果和误差",
      "limitations": "这次验证没有覆盖什么，结论不能外推到哪里",
      "artifact": "evidence/run-01.txt",
      "status": "passed"
    }
  ]
}
```

## Step 6：写初稿

`draft.md` 必须满足：

- 使用已经写入 `brief.md` 的路线和对应篇幅，不靠长引用、代码和参考列表注水；
- 5–9 个 H2，且每节有明确的信息增量；
- 开头让读者看见一个现场、冲突、真实测试动机或生产问题，不写背景综述；
- 只保留一个中心判断，用完整事实链、分类框架或真实项目把它撑住；
- `selected-analysis` 和 `selected-framework` 至少两张有效图表；`selected-hands-on` 至少四张真实截图、对照、输出或图表；
- 文中必须看得见作者做过什么：读出的原文细节、真实输入输出、失败修复、自己建立的模型或明确取舍；
- 明确区分事实、观察、推断和作者判断；
- 逐段使用 `[Sxx]` 引用，不使用“据报道”代替来源；
- 禁止“颠覆性、史诗级、秒杀、彻底改变”等无证据宣传语。

### 去 AI 腔编辑

初稿完成后单独做一轮“人味编辑”，不增加新事实，只改表达：

- 删除每节开头的套话和末尾重复总结，段落直接从事实、场景或判断进入；
- 不机械统计某一种句式；重点删除没有作者观察、对象和后果的模板段落；
- 不为凑排比强行写三点、四层、五个关键；只有真实顺序或互斥分类才编号；
- 标题使用自然判断或读者问题，不把所有标题写成“名词：解释”的同一格式；
- 长短句和段落长度要有变化，连续三个段落不能使用相同句式开头；
- 把“赋能、闭环、底层逻辑、生态、全面升级”等抽象词换成具体的人、动作、对象和后果；
- 允许作者做有证据的取舍和判断，不写四平八稳的“两边都有道理”；
- 大声读一遍；删掉读起来像演讲提纲、咨询报告或产品发布稿的句子。

## Step 7：三次独立审校

审校时不要沿用写作者的自我评价。

### 7.1 事实审校

抽取至少 8 个关键主张写入 `fact-check.json`：

```json
{
  "claims": [
    {
      "id": "C01",
      "claim": "正文中的可核验主张",
      "source_ids": ["S01", "S03"],
      "status": "verified",
      "included_in_article": true,
      "caveat": ""
    }
  ]
}
```

`verified/supported/qualified` 为可接受状态。`pending/unverified` 主张若仍在正文，质量门失败。同步生成便于人工阅读的 `fact-check.md`。

### 7.2 技术审校

检查命令、版本、参数、因果关系、基准条件和术语。把“相关”误写成“因果”、把单次结果写成普遍结论，均需退回修改。

### 7.3 编辑审校

在 `editorial-review.md` 对 7 项各打 0–5 分并给证据：信息增量、推理深度、结构、清晰度、原创综合、读者价值、作者感与自然度。任何一项低于 4，修改一次；最多两轮，仍不达标就保留草稿并报告原因。

## Step 8：运行硬质量门

在安装 Prax 的环境中运行：

```bash
python3 -m prax.content_quality \
  "$ROOT/draft.md" \
  --business-bridge "$ROOT/business-bridge.json" \
  --sources "$ROOT/source-index.json" \
  --fact-check "$ROOT/fact-check.json" \
  --evidence "$ROOT/evidence.json" \
  --profile "<selected-analysis|selected-framework|selected-hands-on>" \
  --json-out "$ROOT/quality-report.json"
```

命令中的 profile 必须与 `brief.md` 一致。`selected-sharp` 通过也不能生成腾讯云精选批次的 `publish-ready.md`。

质量门只按正文实际引用的来源计算域名多样性；只放进研究池、没有进入正文的来源不能抬分。每次证据运行还必须写明 `limitations`，缺少外推边界时按未完成处理。

只有命令退出码为 0、所有 hard gates 通过、机器分至少 `80/100`，并且编辑审校七项都至少 4/5，才能用 Read + Write 生成 `publish-ready.md`。最多修订两轮，不得通过重复段落或虚构来源冲分。

## Step 9：交付

向用户报告：

- 选题、热点分及选择理由；
- 技术到业务链、内容路由以及仍缺的桥接字段；
- 研究来源数、正文实际引用数、第一方引用数；
- 实测方法和证据路径；
- 机器可审计分、编辑七项分；
- `publish-ready.md` 或未通过时的 `draft.md` 路径；
- 仍然存在的局限。

## 安全边界

- 不自动发布到网站、公众号或社交平台；
- 不绕过登录、付费墙、robots 或站点限制；
- 不把社区评论当作事实来源；
- 不伪造浏览、采访、运行结果或引用；
- 不为了达到篇幅门槛重复表达；
- 高风险领域必须有人类主编确认后才能进入发布态。
