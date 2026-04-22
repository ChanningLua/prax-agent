---
name: docs-audit
description: 对比近期代码改动和文档变更，找出"代码改了文档没跟上"的 drift
allowed-tools: [Bash, Read, Write, Grep, Glob, Notify]
triggers: [docs audit, 文档审计, docs freshness, stale docs, 文档过时, 文档 drift, devex]
tags: [documentation, audit, devex, drift]
priority: 7
---

# Docs Freshness Audit

**痛点**：代码改了 40 天了，文档还停在 3 个月前。没人专门盯，自然就 drift。这个 skill 每周扫一次、给出有证据的清单，让技术写作不用手动翻 git blame。

## 何时触发

- cron 每周跑一次
- 用户说："扫一下文档哪些过时了"、"查 docs freshness"
- PR 改了 `src/` 但没改 `docs/` 时触发（需要 hook 配合，本 skill 不负责触发点）

## 输入

- **窗口**：默认 30 天（`--since="30 days ago"`），用户可覆盖
- **源目录**：默认 `src/`、`core/`、`tools/`、`lib/` 里实际存在的
- **文档目录**：默认 `docs/` + `README.md` + `CHANGELOG.md`

实际检测前先用 `Glob` 探一下项目里实际的目录布局，不要假设。

## 输出

一个 markdown 报告 + 可选 GitHub issue：

```
.prax/reports/docs-audit-<YYYY-MM-DD>.md
```

**不**自动改文档（写作是人的事）。**不**删已有报告（历史归档有价值）。

## 工作流程

### Step 1：摸底

```bash
# 列出项目里实际的源目录和文档目录
ls -d src/ core/ tools/ lib/ docs/ 2>/dev/null
find . -maxdepth 2 -name "README*.md" -not -path "./node_modules/*"
```

### Step 2：找近期改过的源文件

```bash
git log --since="30 days ago" --name-only --pretty=format: -- <source-dirs> \
  | sort -u \
  | grep -v '^$' \
  | grep -E '\.(py|ts|tsx|js|jsx|go|rs|java|kt|md)$'
```

`.md` 也保留——文档自己也可能"过时"（比如指向已删除的文件）。

### Step 3：对每个源文件查文档提及

```bash
# 对 src/auth.py，grep docs/ 和 README
SOURCE=src/auth.py
STEM=$(basename $SOURCE .py)      # auth
grep -rln "$SOURCE\|$STEM" docs/ README*.md CHANGELOG.md 2>/dev/null
```

四种情况分类：

| 场景 | 判定 | 列入报告? |
|---|---|---|
| 源文件新增（无 history）+ 文档无提及 | 可能是内部实现，skip | ❌ |
| 源文件改过 + 文档**也**改过（窗口内） | 健康 | ❌ |
| 源文件改过 + 文档**完全没提过** | 可能是内部模块，不是公开 API | ⚠ 低优先级 |
| 源文件改过 + 文档提过但文档未改 | **真 drift** | ✅ 高优先级 |

### Step 4：生成报告

模板：

```markdown
---
generated_at: 2026-04-22T09:00:00+08:00
window: "last 30 days"
repo_head: <short sha>
stale_count: 7
---

# Docs Freshness Audit — 2026-04-22

扫描窗口：过去 30 天。发现 **7 处可能的文档过时**。

## 🔴 高优先级（文档提及 + 代码改了 + 文档没改）

### 1. `src/auth.py` ↔ `docs/authentication.md`

**证据**：

- 源文件最近 commit：
  ```
  a1b2c3d 2026-04-20 feat(auth): migrate session cookies to SameSite=Strict
  d4e5f6g 2026-04-15 fix(auth): token refresh race
  ```
- 文档最后修改：2026-02-10（64 天前）
- 文档中仍提到：SameSite=Lax（第 45 行）

**建议**：更新 `docs/authentication.md` 的 cookie 配置段。

### 2. ...

## 🟡 低优先级（代码改了但文档没提过）

- `core/cache.py`（3 commits in window）—— 可能是内部模块，酌情是否要补文档

## 📊 统计

- 扫描源文件：124
- 窗口内改动：18
- 真 drift：7
- 可能内部：11
```

### Step 5（可选）：开 GitHub issue

如果 `gh` 可用 **且** 用户配置允许（`.prax/docs-audit.yaml: auto_issue: true`）：

```bash
gh issue create \
  --title "Docs drift: 7 files need updating" \
  --body-file .prax/reports/docs-audit-2026-04-22.md \
  --label "docs,maintenance"
```

**默认不开 issue**——避免噪音。用户明确开关才做。

### Step 6：通知

若 `.prax/notify.yaml` 有 `devex` channel：

```
Notify(
  channel = "devex",
  title = "Docs audit: X files drifting",
  body = <报告的 🔴 段摘要 + 报告路径>,
  level = "warn" if stale_count > 0 else "info",
)
```

## 硬约束

1. **每项必须给证据**——三行 git log + 文档最后修改时间。不能空口说"可能过时"。
2. **不改文档**——只报告。文档怎么写是人的事。
3. **新文件不报 stale**——没 history 的源文件，默认跳过。
4. **跳过生成文件**：`*.lock`、`__pycache__`、`node_modules`、`.venv`、`dist/`、`build/`
5. **报告只写不删**——`.prax/reports/` 下的历史报告保留，用户自己清理。

## 脾气

- 误报**多**好过漏报**少**：technical writers 宁愿过滤 20% 无关项，也比错过真 drift 强
- 报告中列的每个源文件都要带最近 3 个 commit sha，让读者能 `git show` 验证
- 低优先级那段只列前 20 条，超了折叠成"还有 N 个"

## 配置（可选）`.prax/docs-audit.yaml`

```yaml
window_days: 30
source_dirs: ["src", "core", "tools", "lib"]
doc_dirs: ["docs"]
include_files: ["README.md", "README.zh-CN.md", "CHANGELOG.md"]
skip_patterns: ["**/migrations/**", "**/__generated__/**"]
auto_issue: false
notify_channel: devex   # 空字符串或不设 = 不通知
```

## 和其他 skill 的接力

- 上游 `release-notes`：发版前跑一次 docs-audit，把 drift 塞进 "## Documentation" 段
- 上游 `pr-triage`：PR 改了 src/ 但没改 docs/，可以作为 triage 的一条"需关注"信息
