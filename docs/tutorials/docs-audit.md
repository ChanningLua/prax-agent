# 傻瓜式教程 · 文档 Drift 审计

**面向**：第一次用 Prax 做文档维护的人——DevEx、技术写作、小团队的"写文档那个人"。

**你是谁**：你负责一个开源项目或公司内部库的文档。代码三个月改了 40 次，文档大概更新了 5 次。你心虚："肯定有地方对不上了，但不知道是哪。"

**完成后你会拥有**：一份带证据的 "stale docs 清单"——哪个源文件最近改了、对应文档什么时候最后改的、docs 里哪一段仍然说的是旧行为。每周自动扫一次，不用自己手动翻。

---

## 前置

- [Getting Started](../getting-started.md) 走完
- 仓库是 git 仓库（非常重要——本技能靠 `git log` 拿证据）
- 有一定量的文档（至少有个 README + 几篇 docs/）。全空仓库跑不出有意义的结果。

---

## Phase 1 · 在本 Prax 仓库跑一次试水（5 分钟）

本仓库自带 `docs/` 和 `core/` / `tools/` 目录，正好拿来做 demo。

### Step 1：cd 到本仓库

```bash
cd /path/to/prax-agent     # 换成你 clone 的本仓库路径
```

没 clone？

```bash
git clone https://github.com/ChanningLua/prax-agent.git
cd prax-agent
```

### Step 2：让 Prax 跑 docs-audit

```bash
prax prompt "触发 docs-audit 技能：扫过去 30 天的代码改动 vs docs/ 里的文档，生成 drift 报告"
```

**等 1-2 分钟**。Prax 会：

1. `git log --since="30 days ago" --name-only` 列出改过的源文件
2. 对每个源文件，`grep` `docs/` 下是否有提到
3. 对每个"docs 有提但 docs 没一起改"的 → 标 🔴 高优
4. 对每个"docs 完全没提"的 → 标 🟡 低优
5. 写 `.prax/reports/docs-audit-<YYYY-MM-DD>.md`

**完成提示**会类似：

```
扫描完成：
- 源文件 164 个（过去 30 天改过 47 个）
- 🔴 高优 drift：3 项
- 🟡 低优候选：12 项
- 报告：.prax/reports/docs-audit-2026-04-22.md
```

### Step 3：读报告

```bash
cat .prax/reports/docs-audit-*.md
```

**应该看到**类似结构（具体项因仓库实际状态而异）：

```markdown
---
generated_at: 2026-04-22T...
window: "last 30 days"
repo_head: bb07839
stale_count: 3
---

# Docs Freshness Audit — 2026-04-22

扫描窗口：过去 30 天。发现 3 处可能的文档过时。

## 🔴 高优先级（文档提及 + 代码改了 + 文档没改）

### 1. `core/middleware.py` ↔ `README.md`

**证据**：

- 源文件最近 commit：
  ```
  d0164f4 2026-04-21 fix(middleware): replace VerificationGuard with shared ChangeTracker
  ```
- 文档最后修改：2026-04-17
- 文档中仍提到：`VerificationGuardMiddleware`（README 第 XX 行）

**建议**：README 里对 middleware 的描述需要更新到 ChangeTracker。

### 2. ...

## 🟡 低优先级
- `tools/notify.py`（1 commit in window）—— 可能是新模块没必要 README 提
...
```

**每一项都有 git log 证据行**——不用你 re-verify 可信不可信，自己 `git show` 就能验证。

### Phase 1 排错

| 症状 | 解法 |
|---|---|
| `window 内无改动` | 仓库最近 30 天没 commit。改 prompt 说"窗口 180 天"试试 |
| 报告里一片 🟡、0 条 🔴 | 代码改了但 docs 完全没提过该模块——这不是 drift 是"从没写过文档"。酌情 |
| Prax 说找不到 git | 当前目录不是 git 仓库。`pwd` 看确认位置 |

---

## Phase 2 · 换成你自己的仓库（5 分钟）

本仓库只是 demo。换到你自己项目里，**什么都不用改**，再跑一次 Step 2 命令。

```bash
cd /path/to/your-own-repo
prax prompt "触发 docs-audit 技能：扫过去 30 天的代码改动 vs docs/ 里的文档，生成 drift 报告"
```

Prax 自动探测你仓库里实际存在的源目录（`src/ / lib/ / core/ / tools/` 等）和 doc 目录（`docs/ / README* / CHANGELOG.md`）。不需要配置。

### 如果你的 doc 在奇怪的地方

有些团队把 docs 放在 `documentation/` 或 `wiki/` 而不是 `docs/`。建一个配置文件：

```bash
cat > .prax/docs-audit.yaml <<'YAML'
window_days: 30
source_dirs: ["src", "lib", "app"]     # 替换成你真实的源目录
doc_dirs: ["documentation"]             # 替换成你 docs 所在
include_files: ["README.md", "docs/index.md"]
skip_patterns: ["**/migrations/**", "**/__generated__/**"]
auto_issue: false
notify_channel: devex                   # 不想通知就留空
YAML
```

下次跑 skill 会读这个文件。

### Phase 2 排错

| 症状 | 解法 |
|---|---|
| Prax 扫的源目录不对 | 写 `.prax/docs-audit.yaml` 显式声明 |
| 误报率高（一堆无关项） | 把明显的生成/第三方目录加进 `skip_patterns` |
| 漏报（我肉眼看到的 drift 没列出来） | Prax 扫的是"文件 stem 被提及"，如果你的文档用的是抽象概念而不是文件名，它发现不了。把该概念关键词加进 `.prax/docs-audit.yaml` 的 `include_keywords`（如果你的版本支持）；或接受这个边界 |

---

## Phase 3 · 每周自动扫 + 自动开 issue（10 分钟）

### Step 1：配 cron 任务

```bash
prax cron add \
  --name docs-audit-weekly \
  --schedule "0 9 * * 1" \
  --prompt "触发 docs-audit 技能扫过去 7 天（更短窗口，周频跑）" \
  --session-id cron-docs-audit \
  --notify-on success \
  --notify-channel devex
prax cron install
```

每周一早上 9:00 跑。

### Step 2：把报告推飞书

跟 [support-digest Step 8](./support-digest.md) 一样：拿 webhook → 写 `.prax/notify.yaml` 加 `devex` channel → 测一条。跳过步骤。

### Step 3（可选）：让 Prax 自动开 GitHub issue

小团队手动看报告就够。组织想省心，开 `auto_issue`：

```yaml
# .prax/docs-audit.yaml
auto_issue: true
```

下次跑完 skill 会：

1. 读报告
2. 调 `gh issue create` 把报告作为 issue body，标签 `docs,maintenance`

**前提**：`gh` CLI 装好且登录（`gh auth status` 绿）。

### Step 4（可选）：发版前先跑一次

发版前想一并把 docs drift 列进 release notes？连锁调用：

```bash
prax prompt "先跑 docs-audit 技能，把结果作为输入给 release-notes 技能生成 v0.5.0 的发版说明，drift 部分作为 '## Documentation' 段"
```

Prax 会把两个 skill 串起来。

### Phase 3 排错

| 症状 | 解法 |
|---|---|
| cron 跑了但报告没写 | 看 `.prax/logs/cron/docs-audit-weekly-*.log`，多半是路径问题（cron 不在你仓库根目录跑）。cron install 时确保你在仓库根 |
| `gh issue create` 403 | `gh auth status` 看权限；可能你的 token 没 repo write |

---

## 怎么读报告更高效

**跑出一张清单后，按这个顺序过**：

1. 🔴 段 top 3 → 当周必修，指派给 doc owner
2. 🔴 段其余 → 下周修
3. 🟡 段快速扫 → 有没有"其实该写文档但一直没写"的候选，记个 backlog
4. 趋势：连续三周都有 🔴 累积 → 说明 doc 节奏跟不上 code 节奏，考虑调整流程

---

## 和其他 skill 的配合

- 发版前跑（[release-notes](./release-notes.md)）：把 drift 修好的放进 `## Documentation` 段
- PR 合入前跑 [pr-triage](../recipes/pr-triage.md)：如果 PR 改了 src/ 但 docs/ 没跟，triage 标风险
- 发现 drift 集中在某个模块 → 单独开个 refactor issue，Prax 可以继续帮你 draft 改动方案

---

## 硬边界（Prax 不会做的事）

- 不改任何文档（只报告）
- 不删历史报告（`.prax/reports/` 下自己清理）
- 不扫 `node_modules / __pycache__ / dist / .venv`
- 默认**不**自动开 issue（`auto_issue: false`），要你 opt-in

问题：<https://github.com/ChanningLua/prax-agent/issues>
