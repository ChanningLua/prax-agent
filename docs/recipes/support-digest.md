# Recipe · Support Ticket Digest

**目标用户**：PM、客服主管、产品运营

**解决的问题**：每天 200 条工单，PM 不可能逐条看完。但纯计数又看不出"今天有新涌出的抱怨"。这个 skill 做三件事：脱敏、聚类、识别趋势，产出一屏能读完的简报。

**关键卖点（合规导向）**：**全部本地处理，零外部 API 调用**。数据不上云。PII 在进入任何 LLM 上下文前已经脱敏。适合金融、医疗、政务等敏感场景。

## 前置

- 从客服系统导出工单的 JSON/CSV（Zendesk、Freshdesk、Intercom 等都支持 export）
- 放到 `.prax/inbox/tickets-<YYYY-MM-DD>.json`
- 示例数据：`docs/recipes/support-digest/sample-tickets.json`（8 条虚构工单，可用来冒烟）

工单 JSON 的最小字段：

```json
[
  {
    "id": "T-10342",
    "created_at": "2026-04-21T14:22:00+08:00",
    "severity": "high",
    "category": "billing",
    "subject": "...",
    "body": "...",
    "customer_email": "..."
  }
]
```

字段缺失 skill 会尽量宽容；但 `id / created_at / body` 必须有。

## 冒烟：用示例数据跑一次

```bash
mkdir -p .prax/inbox
cp <prax-install-dir>/docs/recipes/support-digest/sample-tickets.json \
   .prax/inbox/tickets-2026-04-21.json

prax prompt "生成 2026-04-21 的客服简报"
```

看输出：

```bash
cat .prax/vault/support/2026-04-21/digest.md
cat .prax/vault/support/2026-04-21/tickets-redacted.json  # 脱敏后的结构化数据
```

你会看到：`billing` 类 3 条最紧张，`T-10349` 涉及律师话术被标高优先级，email / 手机号都脱敏成 `f***@e***.com` / `138****5678`。

## 每日自动跑

```bash
prax cron add \
  --name support-digest-daily \
  --schedule "0 9 * * *" \
  --prompt "生成昨日客服简报（基于 .prax/inbox/ 里最新的 tickets-*.json 文件）" \
  --session-id cron-support-digest \
  --notify-on success \
  --notify-channel pm-team
prax cron install
```

每天 9:00 跑；简报发 `pm-team` channel（可以是飞书群或 PM 个人邮箱，走 M1 的 NotifyTool）。

前提：有人（或自动脚本）每天把昨日工单导出成 `tickets-<yesterday>.json` 放 `.prax/inbox/`。Prax 不替你从 Zendesk/Freshdesk 拉数据——这是 IT/客服的事。

## 脱敏策略

Step 2 在**任何处理之前**先脱敏。默认规则：

- email: `foo@bar.com` → `f***@b***.com`
- 手机号（大陆 11 位 / 国际 +前缀）: 中间 4 位替换 `****`
- body 内嵌的 email / 手机号: 正则替换
- body 内嵌的银行卡号 / 身份证号 / SSN: `[REDACTED]`

想加自定义模式：`.prax/support-digest.yaml` 里的 `redaction.extra_regex`。

## 数据去向

- 原始工单 → **处理完立即归档**到 `.prax/inbox/archive/tickets-<date>.json`，避免下次重复处理
- 脱敏版 → 留在 `.prax/vault/support/<date>/tickets-redacted.json`，给下游 BI / 周报复用
- Digest → `.prax/vault/support/<date>/digest.md`，一屏读完

所有数据都在你的本地 workspace 里。

## 和其他 skill 的联动

- 简报里反复出现"文档不清楚"的抱怨（category=docs 激增）→ 触发 `docs-audit`
- 某 category 今日翻倍且昨晚刚 merge 过相关 PR → PM 可以拿 digest 叫 `pr-triage` 回看那次 PR
- 发版前看上周 digest 趋势 → `release-notes` 可以把"本周修复的投诉热点"写进去

## 硬边界

- **本地处理**：不调 OpenAI Embedding、不发 HTTP 到任何分析平台
- **脱敏先行**：PII 永远不进入 LLM 上下文
- **highlights 5 条封顶**：宁可漏不要淹
- **body 摘录 100 字封顶**：不要把完整用户原话贴进 digest
- **不自动回复工单**、**不自动关工单**、**不自动开 refund**——所有外部动作由人触发
