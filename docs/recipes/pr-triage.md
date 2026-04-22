# Recipe · PR Triage Bot

**目标用户**：Engineering lead、代码审查人、QA 主管

**解决的问题**：每天 10 个 PR 进来，你不可能每个都 deep-review。但全丢给 reviewer 又有漏网——某个小 fix 把 auth 流程给改了没人注意。PR Triage 把"表层分类 + 真跑测试 + 依赖变化"一股脑做掉，让你把专家时间花在真需要的 PR 上。

**和纯 LLM review 的区别**：这个 skill **真的 checkout PR 分支、真的跑 pytest**。基于运行结果而不是文本推测来给结论。

## 前置

- `gh` CLI 装好且登录（`gh auth status` 绿）—— 没装也能跑降级路径，但功能差
- 仓库能跑 `pytest` / `npm test` / `cargo test` / `go test`（至少一个）
- PR 是当前仓库的（跨仓库的暂不支持）

## 一次性触发

```bash
prax prompt "triage #42"
# 或
prax prompt "审查 PR https://github.com/ChanningLua/prax-agent/pull/42"
```

产出：

```
.prax/pr-triage/pr-42-<YYYYMMDD-HHMMSS>.md
```

内容：分类、风险评分、PR 分支 vs base 的测试对比、新增依赖列表、建议的审查要点。

## 看一眼报告

```bash
cat .prax/pr-triage/pr-42-*.md
```

满意就人工 review 重点文件。不满意、想重跑 → 直接再跑一次（报告会时间戳命名，不会覆盖历史）。

## 每天自动扫 open PRs

```bash
prax cron add \
  --name pr-triage-daily \
  --schedule "0 8 * * 1-5" \
  --prompt "对 open 状态、过去 24 小时内有新 commit 的 PR 逐个 triage，把报告放在 .prax/pr-triage/ 下，Notify 推 eng-leads" \
  --session-id cron-pr-triage \
  --notify-on failure \
  --notify-channel eng-leads
prax cron install
```

每个工作日早上 8 点扫一遍，notify_on=failure 意思是 triage 流程出错才打扰你（成功的报告自己去 `.prax/pr-triage/` 看）。

## 风险评分怎么来的

每个触发规则 +分，累加：

| 规则 | +分 |
|---|---|
| patch > 500 行 | +2 |
| patch > 1000 行 | +3（累加） |
| 10+ 文件 | +2 |
| 改 package.json / requirements.txt 且新增 dep | +2 |
| 改 auth / security / permission 相关 | +2 |
| 改 CI（`.github/workflows/`） | +2 |
| 改数据库迁移 | +3 |
| PR body 空 | +1 |
| 没伴随测试 | +2 |

0-2 低、3-5 中、6+ 高。高风险 PR 的 Notify level=warn，更显眼。

## 测试结果的解读

Skill 会跑两次：PR 分支 + base 分支。四种组合：

| PR | base | 结论 |
|---|---|---|
| ✅ | ✅ | 最健康。PR 无回归。 |
| ❌ | ✅ | **PR 引入了失败**。明确问题。 |
| ✅ | ❌ | PR 修好了问题（或巧合绕过） |
| ❌ | ❌ | 基线本身坏。PR 的锅暂时说不清。 |

## 硬边界

- **不自动 approve / merge / close**
- **不自动在 PR 发 comment**（你要发让 reviewer 自己选一段粘）
- **patch > 2000 行直接跳过测试**，报 "过大，建议拆分"
- **不联外网查 CVE**——只列新增依赖让你自己查

## 没 `gh` 怎么办

降级路径：skill 会尝试 `git fetch origin pull/<N>/head:pr-<N>` 然后本地 diff。能拿到就继续 triage，拿不到就只做分类和手动审查清单（跳过真跑测试那步）。报告会标 "degraded mode: tests skipped"。
