---
name: pr-triage
description: 给单个 PR 做代码感知 triage —— 分类 / 跑测试 / 扫依赖 / 产出审查笔记
allowed-tools: [Bash, Read, Write, Grep, Glob, VerifyCommand, Notify]
triggers: [pr triage, 审 pr, 审查 pr, pr review, 分类 pr, triage pr, pull request review]
tags: [pull-request, code-review, ci, devex, verify-first]
priority: 8
---

# PR Triage Bot

**Prax 的杀手锏在这**：不只"让 LLM 看 diff 给点评"，而是**真的 checkout 出来、真的跑测试、真的看新增依赖有没有 CVE**。结果比纯 LLM 评论可信一个量级。

## 何时触发

- 用户粘 PR URL 或说"triage #42"
- cron 每天早上 8 点扫 open PRs
- GitHub webhook 触发（需要额外 hook 配置，本 skill 不负责触发）

## 输入

- **必需**：PR URL 或 `#NNN`（本仓库的话）或 PR 分支名
- **可选**：仓库路径（默认当前 cwd）

## 预检（不满足就停）

```bash
# 1. 在 git 仓库里
git rev-parse --show-toplevel

# 2. 有 gh CLI 吗？
command -v gh && gh auth status 2>/dev/null
```

没 `gh` 时走 **降级路径**（见 Step 2b）。没 git 仓库直接报错。

## 输出

一份 markdown 审查笔记：

```
.prax/pr-triage/pr-<NNN>-<YYYYMMDD-HHMMSS>.md
```

**不**自动 approve / merge / close / comment 到 GitHub。**所有**外部动作都必须用户明确再触发一次。

## 工作流程

### Step 1：拿 PR metadata

```bash
# 完整路径
gh pr view <url> --json number,title,body,files,author,baseRefName,headRefName,additions,deletions,commits

# 提炼
NUM=$(echo $JSON | jq -r '.number')
TITLE=$(echo $JSON | jq -r '.title')
BASE=$(echo $JSON | jq -r '.baseRefName')
HEAD=$(echo $JSON | jq -r '.headRefName')
STATS="+$(echo $JSON | jq -r '.additions'),-$(echo $JSON | jq -r '.deletions')"
FILES=$(echo $JSON | jq -r '.files | length')
```

### Step 2：拿 diff 本体

```bash
gh pr diff <url> > /tmp/pr-<NUM>.diff
wc -l /tmp/pr-<NUM>.diff
```

### Step 2b：gh 不可用的降级

没 `gh` CLI 时：

```bash
# 本仓库的 PR：如果本地 remotes 里有 pull/<num>/head refspec
git fetch origin pull/<NUM>/head:pr-<NUM> 2>/dev/null
git diff origin/<base>...pr-<NUM> > /tmp/pr-<NUM>.diff
```

如果连 fetch 也不行（外网受限）→ 退化成**只读远端分支名**模式，不跑测试，只产出分类 + 手动审查清单。

### Step 3：分类（硬枚举）

用 PR title + 首个 commit message + files-changed 启发式：

| 分类 | 证据 |
|---|---|
| `feature` | title 含 `feat(...)` / 新文件多 / `+100` 以上 |
| `bug` | title 含 `fix(...)` / 行数均衡增删 |
| `refactor` | title 含 `refactor` / 纯结构调整 |
| `chore` | title 含 `chore` / 仅 config/yaml |
| `docs` | 仅 `*.md` 变更 |
| `hotfix` | 含 `hotfix` 或 `urgent` |

一个 PR 可以多标签，取最强一个作为主分类。

### Step 4：风险评分

硬规则，每触发一条加一分：

| 规则 | 分数 |
|---|---|
| patch > 500 行 | +2 |
| patch > 1000 行 | +3（累加） |
| 触达 >10 个文件 | +2 |
| 改 `package.json`/`requirements.txt`/`Cargo.toml`/`go.mod` 且有新增依赖 | +2 |
| 改 `auth`/`security`/`permission`/`middleware` 相关文件 | +2 |
| 改 CI / `.github/workflows/` | +2 |
| 改数据库迁移文件 | +3 |
| PR body 空 | +1 |
| 没测试伴随（仅 src 没 test） | +2 |

0-2 分：低；3-5：中；6+：高。

### Step 5：真跑测试（Prax 核心）

```bash
git fetch origin pull/<NUM>/head:prax-triage-<NUM>
git checkout prax-triage-<NUM>

# 用 VerifyCommand 跑，限时 5 分钟
VerifyCommand(command="pytest -q", timeout=300)
# 或 npm test / cargo test / go test — 从 Step 2 的 files 推断
```

**失败不代表 PR 坏**——可能是仓库本身坏的。拿 `base` 做对照：

```bash
git checkout <BASE>
VerifyCommand(command="pytest -q", timeout=300)
```

- PR 失败 + base 通过 → **PR 引入了问题**（明确）
- PR 失败 + base 也失败 → 仓库本身坏（标 "baseline broken"）
- PR 通过 + base 通过 → ✅
- PR 通过 + base 失败 → **PR 修好了问题**（可能）

跑完**必须 checkout 回原分支**，避免污染用户工作区：

```bash
git checkout -
git branch -D prax-triage-<NUM>
```

### Step 6：依赖扫描

```bash
# 对 diff 找新增 import / require
grep -E '^\+[[:space:]]*(import|from|require|use )' /tmp/pr-<NUM>.diff | head -30

# package.json / requirements.txt / Cargo.toml 新增条目
git diff origin/<base>...<head> -- package.json requirements.txt Cargo.toml go.mod
```

列出来不代表有漏洞——只列给 reviewer，让 reviewer 决定要不要查 CVE。

### Step 7：写审查笔记

```markdown
---
pr: 42
title: feat(notify): add NotifyTool
author: @foo
generated_at: 2026-04-22T10:05:00+08:00
stats: "+320, -15, 4 files"
---

# PR #42 Triage

## 分类
- 主：**feature**
- 副：docs

## 风险评分：3（中）

触发规则：
- patch = 305 行 (+1)
- 新增依赖：httpx (+2)

## 测试结果
- PR 分支 pytest -q：✅ 1929 passed
- 基线 base `main` pytest -q：✅ 1922 passed
- **结论**：PR 引入 7 个新通过的测试，无回归

## 新增依赖
- Python: `httpx` (已有 dep)
- 无 package.json 变更

## 审查要点
1. `tools/notify.py:38` Provider 基类 — 检查是否预留扩展点
2. `tools/notify.py:105` SMTP 密码从 env 读 — 确认无 YAML 硬编码
3. `tests/unit/test_notify.py` 23 个测试 — 覆盖所有 provider 分支

## 推荐动作
- [ ] 人工审 `tools/notify.py:__init__` 的接口稳定性
- [ ] 确认 `notify.yaml` schema 文档已更新

(Prax pr-triage 不自动 approve/merge/close — 所有 GitHub 动作由人触发)
```

### Step 8：通知

若 `.prax/notify.yaml` 有 `eng-leads` channel：

```
Notify(
  channel = "eng-leads",
  title = "PR #<NUM> triage: <level>",
  body = <笔记的分类 + 风险评分 + 测试结果段>,
  level = "error" if tests_failed else ("warn" if risk >= 6 else "info"),
)
```

## 硬约束

1. **不 approve / 不 merge / 不 close / 不 comment 到 GitHub**——只本地写笔记
2. **跑完测试必须 checkout 回原分支**，`-D` 临时分支
3. **外部 API 不触达**（CVE 扫描、依赖版本查询都只列出，让人去查）
4. **失败路径必须有降级**——gh 不可用、网络不通、测试跑不起来，都要能产出部分笔记
5. **patch > 2000 行时**（硬上限）直接标记 `level=warn`，笔记里写"PR 过大，建议拆分，本工具不对此类 PR 跑完整测试"，跳过 Step 5

## 和其他 skill 的接力

- `docs-audit`：PR 改 src/ 没改 docs/ 时，triage 笔记里引用 docs-audit 的输出
- `release-notes`：triage 过的 PR 在发版时能直接落入 release-notes 的 commit 分类

## 典型调用

```
用户：triage #42

→ skill 跑 gh pr view + gh pr diff
→ 分类 = feature，risk = 3 (patch 320 行 + 新增 httpx 依赖)
→ git fetch pull/42/head → pytest -q → 1929 pass
→ git checkout main → pytest -q → 1922 pass
→ 结论：PR 无回归，引入 7 个新测试
→ 写 .prax/pr-triage/pr-42-20260422-100500.md
→ Notify eng-leads channel (info)
→ 回用户：分类 feature，风险中，测试通过，笔记在 .prax/pr-triage/pr-42-...md
```
