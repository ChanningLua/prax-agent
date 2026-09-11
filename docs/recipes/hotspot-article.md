# 热点与需求到精选文章

`hotspot-article` 把现有 `ai-news-daily` 的热点池升级为一条可审计的深度文章流水线。它不只追新闻，还从客户项目、搜索问题、工单、招聘变化和长期决策中找题。有 AutoCLI 就复用登录态来源，没有也可使用 Prax 的搜索和抓取能力。

## 目标

腾讯云精选样本基线见 [tencent-selected-benchmark.md](./tencent-selected-benchmark.md)。按文章类型选择门槛：

| 路线 | 正文有效字符 | H2 | 关键证据 |
|---|---:|---:|---|
| `selected-analysis` 重大事件精读 | 6,000–12,000 | 5–9 | 完整事实链、原文细节、机制、至少两张图表 |
| `selected-framework` 架构/组织框架 | 5,500–11,000 | 5–9 | 分类轴、决策矩阵、贯穿场景、反例 |
| `selected-hands-on` 产品/项目实战 | 4,500–9,000 | 5–9 | 至少两次真实运行、失败修复、至少四张真实产物 |

这些指标回答的是“证据链和最低完成度是否达标”，不是对文采、洞察或传播效果的自动承诺。`selected-sharp` 只用于技术短稿，不进入精选批次。

## 选题不是只看热搜

默认周内容组合：

| 轨道 | 比例 | 典型问题 |
|---|---:|---|
| 时事 `event` | 30% | 发生了什么，对现有系统有什么影响 |
| 需求 `demand` | 50% | 客户、团队或用户正在怎么做、怎么选 |
| 常青 `evergreen` | 20% | 架构、成本、迁移、排障和培养方法 |

每个精选选题必须能写清楚：谁在什么场景下遇到问题、现在怎么处理、要做什么决定、不处理的代价是什么。发布初期的点赞和评论只能算注意力信号；还要用搜索问题、客户 brief、Issue、工单或岗位变化验证需求。

时事还要经过一条硬桥接链：

```text
来源信号
  → 技术能力或约束具体变了什么
  → 影响哪个业务流程
  → 决策人需要采用、延期、自建、采购或停止什么
  → 用什么方法验证
  → 看哪些成功指标
  → 哪些情况下不适用
```

七个环节会写入 `business-bridge.json`。缺少任何一项，时事题只能进入 `daily-digest`；需求与常青题进入 `research-brief` 补证据。热度不能绕过这道门。

选题还要改变预算、权限、架构、岗位或跨团队流程，并能说明判断错误的后果。单个协议字段、局部版本变化、某个仓库去掉一层组件和厂商小功能，默认进入技术短帖；没有真实迁移或重大采购影响时，不靠扩写升格。

正式研究前还要检查目标社区近 30 天精选内容。同一事件、中心结论和业务落点已经有更完整文章时，除非拥有明显更强的原创证据，否则换题。

单独运行机会评分：

```bash
python3 -m prax.topic_opportunity \
  topic-candidates.json \
  --json-out opportunity-report.json \
  --bridge-out business-bridge.json
```

评分器把真实需求放到 45% 权重，并按 `event 30% / demand 50% / evergreen 20%` 的目标补内容组合缺口。业务场景不完整的候选不会进入精选长文。

## 文风要求

精选档默认包含一轮单独的“去 AI 腔编辑”。它不会靠口语词或故意写错字伪装成人，而是处理更具体的问题：

- 删除“首先/其次/最后”“不是……而是……”等高频模板连接；
- 避免每节都用概括句开头、总结句收尾；
- 只在存在真实顺序或分类时编号，不为了排比强行凑三点；
- 让标题、段落和句子长短有变化；
- 把“赋能、闭环、底层逻辑、生态”等抽象词落到具体动作和后果；
- 允许作者基于证据做明确取舍，不用四平八稳的套话回避判断。

质量报告中的 `formulaic_style_count` 是提醒信号，不是文采分。最终仍由独立编辑检查“作者感与自然度”。

## 一次运行

```bash
prax prompt "运行 hotspot-article：追踪最近 48 小时 AI Agent 热点，面向中文开发者，完成一篇精选档深度文章"
```

如果已经有明确选题：

```bash
prax prompt "运行 hotspot-article：围绕 <主题> 做深度研究和实测，对标精选文章，但不要自动发布"
```

默认产物位于：

```text
.prax/vault/hotspot-articles/<date>/<topic-slug>/
```

重点检查：

- `topic-candidates.json`：三轨候选、需求证据与业务场景；
- `opportunity-report.json`：机会分、组合缺口与最终选题；
- `business-bridge.json`：时事/需求信号如何变成技术判断、业务决策和验证指标；
- `demand-brief.md`：这篇文章服务谁、解决什么决策；
- `source-index.json`：研究池和每条来源支持什么；
- `evidence.json` 与 `evidence/`：真实验证方法、结果和原始产物；
- `fact-check.json`：关键主张逐条核验状态；
- `quality-report.json`：机器硬门结果；
- `publish-ready.md`：仅全部通过后存在。

## 单独检查文章

```bash
python3 -m prax.content_quality \
  .prax/vault/hotspot-articles/2026-07-29/topic/draft.md \
  --business-bridge .prax/vault/hotspot-articles/2026-07-29/topic/business-bridge.json \
  --sources .prax/vault/hotspot-articles/2026-07-29/topic/source-index.json \
  --fact-check .prax/vault/hotspot-articles/2026-07-29/topic/fact-check.json \
  --evidence .prax/vault/hotspot-articles/2026-07-29/topic/evidence.json \
  --profile selected-analysis \
  --json-out .prax/vault/hotspot-articles/2026-07-29/topic/quality-report.json
```

退出码：

- `0`：所有硬门通过且机器可审计分达到 80/100；
- `1`：文章可读取，但至少一个门槛未通过；
- `2`：输入文件不存在或 JSON 无法解析。

不要靠重复段落修复分数。先看报告中的 `Next fixes`，补原始来源、实测和事实闭环，再处理篇幅。

质量门按正文实际引用的来源计算域名数，研究池里未引用的 URL 不抬分。`evidence.json` 中每次运行必须写 `limitations`，说明没有覆盖的条件和不可外推的结论；缺少这一项时，证据运行不计为完成。
