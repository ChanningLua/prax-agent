---
name: support-digest
description: 本地处理客服工单 JSON 导出，产出脱敏后的每日简报 —— 不出数据、不调外部 API
allowed-tools: [Read, Write, Glob, Grep, Bash, Notify]
triggers: [support digest, 客服简报, 工单简报, ticket digest, 客户反馈, customer feedback, 投诉汇总]
tags: [support, pm, privacy, local-only, digest]
priority: 7
---

# Support Ticket Digest

**痛点**：PM 每天早上要花 20 分钟翻昨天 200 条客服工单，才能知道"哪个 feature 坑多"、"有没有新涌出的抱怨"。这个 skill 把翻 ticket 的动作自动化，**所有数据留在本地**，不调任何外部 API，方便合规严格的团队。

## 何时触发

- cron 每天早上 9 点跑
- 用户手动："生成昨日客服简报"
- 客服系统 CSV/JSON 导出到指定目录触发 hook

## 输入

- **必需**：`.prax/inbox/tickets-<YYYY-MM-DD>.json`（或 `.csv` 也支持）
  - 标准字段：`id / created_at / status / category / subject / body / customer_email / severity`
  - 字段缺失时尽量宽容，但 `id / created_at / body` 必须有
- **可选**：`.prax/support-digest.yaml` 配置（见下方）

Prax **不**负责从 Zendesk / Freshdesk 拉数据——export 是 IT 或客服负责人的事。skill 只做本地文件处理。

## 输出

```
.prax/vault/support/<YYYY-MM-DD>/digest.md
```

外加原始统计数据的结构化副本：

```
.prax/vault/support/<YYYY-MM-DD>/stats.json
```

用于下游工具（BI、周报生成等）复用。

## 工作流程

### Step 1：定位输入

```bash
# 最新的 tickets 文件
ls -t .prax/inbox/tickets-*.{json,csv} 2>/dev/null | head -1
```

没找到 → 停下来告诉用户："请把昨日工单导出为 JSON/CSV 放到 .prax/inbox/tickets-YYYY-MM-DD.json"。

### Step 2：加载 + 脱敏

读进来后**立刻**脱敏用户信息：

| 字段 | 脱敏规则 |
|---|---|
| `customer_email` | `foo@bar.com` → `f***@b***.com` |
| `customer_phone` | `13812345678` → `138****5678` |
| body 里的 email | 正则替换同上 |
| body 里的手机/银行卡号 | 正则识别，全替换成 `[REDACTED]` |
| body 里出现的 SSN / 身份证号 | 全替换成 `[REDACTED]` |

**脱敏后才进入后续处理**——避免不小心把 PII 写进 digest。

### Step 3：分类统计

按 `category` 字段（或从 subject 启发式推断）聚类：

- 计数：今日 vs 昨日 vs 过去 7 天均值
- 热度：新增占比（category_today / category_7d_avg）
- 严重程度：`severity=high` 的占比
- 新类别：今日有而过去 7 天没出现过的 category

### Step 4：识别 top issues

抽取前 **5** 条"关注度高"的工单，标准（按分数降序）：

| 信号 | 分数 |
|---|---|
| severity=high | +3 |
| status=escalated / status=re-opened | +2 |
| 同一 category 今日数量 > 7d_avg × 2 | +2 |
| body 包含 "refund" / "退款" / "退费" | +2 |
| body 包含 "lawyer" / "律师" / "投诉" / "举报" | +3 |
| response_time > 24h（如果有此字段） | +1 |

取前 5 条，每条附**脱敏后**的 body 摘录（≤ 100 字）。

### Step 5：趋势识别

对比"今日 vs 过去 7 天均值"：

- 📈 涨幅 > 50% 的 category：列出来
- 📉 跌幅 > 50% 的 category：列出来（有时代表问题解决了）
- 🆕 今日首次出现：全列

### Step 6：写 digest

模板：

```markdown
---
date: 2026-04-22
ticket_count: 187
category_count: 12
highlights_count: 5
generated_at: 2026-04-22T09:05:00+08:00
---

# 客服简报 · 2026-04-22

## 总览（一句话）
昨日共 **187** 条工单，比 7 日均值 +12%；`billing` 类翻倍，`auth` 类回落。

## 今日亮点（top 5）

### 1. [high] Billing — duplicate charge
- 工单数：23（占 12%）
- 代表性摘要："On 4-20 I was charged twice for the Pro plan. Please refund immediately."
- 建议 owner：@finance-ops

### 2. [escalated] 登录流 — OAuth timeout
- ...

## 趋势

### 📈 涨
- `billing`：23 vs 7d 均值 11（+109%）
- `mobile-app-crash`：8 vs 3（+167%）

### 📉 跌
- `auth`：12 vs 19（-37%）

### 🆕 新出现
- `integration-slack`（6 条）

## 分类统计

| Category | Today | 7d avg | Δ |
|---|---|---|---|
| billing | 23 | 11 | +12 |
| auth | 12 | 19 | -7 |
| ...|

## 报告位置
- Raw 脱敏 JSON：`.prax/vault/support/2026-04-22/tickets-redacted.json`
- Stats：`.prax/vault/support/2026-04-22/stats.json`
- 原始文件已归档到：`.prax/inbox/archive/tickets-2026-04-22.json`
```

### Step 7：归档原文件

```bash
mkdir -p .prax/inbox/archive
mv .prax/inbox/tickets-<date>.json .prax/inbox/archive/
```

避免下次跑重复处理同一天数据。

### Step 8：通知

若 `.prax/notify.yaml` 有 `pm-team` channel：

```
Notify(
  channel = "pm-team",
  title = "客服简报 · 2026-04-22",
  body = <digest 的"总览 + top 5"段>,
  level = "warn" if 有 severity=high >= 5 else "info",
)
```

## 硬约束

1. **本地处理，零外部 API**：不调 OpenAI Embedding、不查 GitHub、不拉 Zendesk。所有逻辑用本地字符串匹配 + LLM 本地推理完成。
2. **先脱敏再处理**：PII 在进入任何 LLM 上下文前必须已替换。脱敏是 Step 2 的第一个子步骤，不得延后。
3. **highlights 上限 5**：多了信息过载。宁愿筛严格。
4. **body 摘录 ≤ 100 字**：不要把完整 body 粘进 digest——digest 是一屏可看完。
5. **原文件必须归档**：处理完后移动到 `.prax/inbox/archive/`，避免下次重复处理。

## 工具选择（很关键）

- 创建**新文件**（`digest.md`、`tickets-redacted.json`）：**必须用 `Write` 工具**（自动 `mkdir -p`）。
- 修改**已存在文件**：用 `Edit` / `HashlineEdit`。
- 归档原 ticket 文件（Step 7）：用 `Bash mv ...` 或 Read+Write+`Bash rm` 的组合。归档失败会导致下次重复处理，不能跳过。
- **`HashlineEdit` / `Edit` 对不存在的路径会 `File not found`** —— 新文件必须走 `Write`。

## 配置（可选）`.prax/support-digest.yaml`

```yaml
input_glob: ".prax/inbox/tickets-*.json"
archive_dir: ".prax/inbox/archive"
output_dir: ".prax/vault/support"
redaction:
  extra_regex: []       # 用户自定义额外 PII 模式
  skip_fields: []       # 某些字段不做 body 内扫描（譬如已是脱敏 field）
highlights_max: 5
severity_field: "severity"
category_field: "category"
lookback_days: 7
notify_channel: pm-team
```

## 和其他 skill 的接力

- `docs-audit` + `support-digest`：工单里反复出现"文档不清楚"的抱怨 → 拿去驱动 docs-audit 的 backlog
- `pr-triage` + `support-digest`：某 PR 上线后对应 category 工单激增 → digest 里提醒回滚评估
