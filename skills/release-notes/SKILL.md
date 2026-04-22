---
name: release-notes
description: 基于 git 历史 + issue 引用生成符合 Keep-a-Changelog 规范的发版说明
allowed-tools: [Bash, Read, Write, Grep, Glob, Notify]
triggers: [release notes, 发版说明, changelog, release, 版本日志, 打 tag, bump version]
tags: [release, changelog, git, devex]
priority: 7
---

# Release Notes Generator

把从上一个 tag 到当前 HEAD（或指定 tag）之间的 git 历史整理成符合 [Keep a Changelog](https://keepachangelog.com/) 的发版说明。

## 何时触发

- 用户说："生成 v1.2.0 的 release notes" / "写一下 0.4.0 的 changelog"
- 用户打了新 tag（例如 `v0.4.0`）后要求写说明
- cron 在 `v*.*.*` tag 推送后触发

## 输入

- **必需**：目标版本号（形如 `v0.4.0` 或 `0.4.0`，自动带/去 `v` 前缀兼容）
- **可选**：上一个 tag（默认用 `git describe --tags --abbrev=0 <target>^`）

## 输出

两个文件都要写：

1. `CHANGELOG.md`：在 `## [Unreleased]` 下方**插入**新条目（不覆盖旧版本）
2. `docs/releases/<version>.md`：独立的发版公告（便于网站、邮件、微信直接引用）

## 工作流程

### Step 1：预检

```bash
# 1.1 必须在 git 仓库里
git rev-parse --show-toplevel

# 1.2 工作区必须干净（有未提交改动时警告，不强制）
git status --porcelain

# 1.3 确认目标版本格式
# 接受 v0.4.0 / 0.4.0；内部统一成 v0.4.0
```

失败就停下来告诉用户，不要继续生成"碰运气"内容。

### Step 2：收集原始数据

```bash
# 上一个 tag
PREV=$(git describe --tags --abbrev=0 <target>^ 2>/dev/null || echo "")

# 范围内的 commit 清单（排除 merge commits）
git log ${PREV:+$PREV..}<target> --oneline --no-merges

# 每个 commit 的完整 message（分类需要 body）
git log ${PREV:+$PREV..}<target> --format='%H%n%s%n%b%n---' --no-merges
```

**没有 PREV**（第一次发版）时，列全部历史，但限 50 条以内（多了就说"initial release"）。

### Step 3：按 Conventional Commits 分类

正则匹配 commit message 首行的前缀：

| 前缀 | CHANGELOG 段 | 优先级 |
|---|---|---|
| `feat(<scope>):` 或 `feat:` | ### Added | 1 |
| `fix(...)` | ### Fixed | 2 |
| `refactor(...)` | ### Changed | 3 |
| `perf(...)` | ### Changed | 3 |
| `docs(...)` | ### Documentation | 4 |
| `chore(...)` / `test(...)` / `ci(...)` | **跳过**，除非是 breaking 或 `chore: bump version` | - |
| `BREAKING CHANGE:` 出现在 body | ### Breaking（**置顶**）| 0 |
| 其他 | ### Other | 5 |

每条在段内按**时间倒序**排列。

### Step 4：抽取 issue / PR 引用

对每条 commit 扫 body 里的 `#(\d+)`、`(#\d+)`、`GH-\d+`：

```bash
# 如果有 gh CLI 且 issue 开着，拿 title 补充上下文
gh issue view <N> --json title,labels -q '.title + " [" + (.labels | map(.name) | join(",")) + "]"'
```

**没 gh CLI 或 issue 404** → 保留 `#N` 原样引用，不崩。

### Step 5：写 CHANGELOG 条目

模板（严格遵守）：

```markdown
## [0.4.0] - 2026-04-22

### Breaking
- **\<scope>**: description（参考 #123 [breaking-change]）

### Added
- \<scope>: description. Refs #42.
- \<scope>: description.

### Changed
- \<scope>: description.

### Fixed
- \<scope>: description. Refs #17.

### Documentation
- \<scope>: description.

### Other
- Miscellaneous changes that don't fit elsewhere.
```

约束：

- **语气**：陈述，过去式（"added", "fixed", "removed"），**不用**"we've added / we plan to"
- **禁忌**：不加"Stay tuned!" / "exciting" / "🎉" / 表情（除非用户明确要求）
- **scope 必填**：commit 没写 scope 就从 files changed 里推断（`tools/` → "tools"；`core/middleware.py` → "middleware"）
- 每条 ≤ 80 字

### Step 6：写 `docs/releases/<version>.md`

模板：

```markdown
# Prax \<version>

_Released 2026-04-22_

<1-2 句话总览：这次主要做了什么>

## Highlights

- \<1-3 条顶级亮点，每条一行>

## What's Changed

<把 CHANGELOG 的 Breaking/Added/Changed/Fixed 段照抄>

## Upgrading

<如果有 breaking，这里写迁移；没有就 "Drop-in replacement, no migration needed.">

## Credits

<用 git log --format='%aN' --no-merges <range> | sort -u 列贡献者>

---

Full diff: https://github.com/ChanningLua/prax-agent/compare/\<prev>...\<version>
```

### Step 7：幂等处理

**关键**：同一版本号**重跑必须覆盖**，不能追加。

执行前：
- 读 `CHANGELOG.md`，用 `## [<version>] -` 正则找到该条目的起止行
- 有就**整块删除**，再插入新版
- 没有就直接在 `## [Unreleased]` 下方插入

`docs/releases/<version>.md` 直接 overwrite。

### Step 8：通知（可选）

若 `.prax/notify.yaml` 有 `release-announce` channel，调 `Notify`：

```
Notify(
  channel = "release-announce",
  title   = "Prax " + version + " released",
  body    = <docs/releases/<version>.md 的 Highlights 段>,
  level   = "info"
)
```

## 硬约束

1. **不打 tag、不 push、不发 npm**：这是内容生成器，不是发布工具。发布动作始终由用户明确触发。
2. **不猜版本号**：用户没指定就问一次，不要自己从 pyproject.toml 读一个版本就开干。
3. **断网可跑**：`gh` CLI 不可达时降级到"只用 git log + commit body"，不报错。
4. **Breaking 必须显式**：只有 commit body 里真的有 `BREAKING CHANGE:` 才放 Breaking 段。别自作聪明把 `refactor(api)` 当 breaking。

## 和其他 skill 的接力

- `docs-audit` 发现"有代码改动但 docs 没跟上"时，生成的 issue 可以成为下一次 release notes 的候选输入
- `ai-news-daily` 负责抓外部内容，**本 skill 专职 on-repo 产出**，边界清晰

## 典型调用

```
用户：生成 v0.4.0 的 release notes

→ skill 跑 git describe 找到 PREV=v0.3.2
→ git log v0.3.2..HEAD 拿到 8 条 commit
→ 按 feat/fix/chore 分成四段
→ 识别 chore(polish) 不属于任何用户可见改动，放 Other
→ 抽取 #123 引用（如果有），gh issue view 补标题
→ 读 CHANGELOG.md 找 [0.4.0]（本次第一次跑，没有）
→ 插入新条目到 [Unreleased] 下方
→ 写 docs/releases/v0.4.0.md
→ 报告用户：CHANGELOG 加了 X 行、docs/releases/v0.4.0.md 写好了
```

用户读完满意就打 tag + 发版，不满意就让 skill 改。
