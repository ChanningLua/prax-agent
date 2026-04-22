# 傻瓜式教程 · 自动生成发版说明

**面向**：第一次用 Prax 发版的团队负责人 / Release manager。

**你是谁**：你是一个 10 人 SaaS 团队的技术负责人。两周没发版了，现在要发 `v1.2.0`，想给团队和客户同时发说明。手写需要：翻 60 条 commit、按类型分类、措辞润色、粘到发布群——1 小时没了。

**完成后你会拥有**：一条命令自动生成 `CHANGELOG.md` 条目 + 独立的发布公告 markdown + 可选的飞书群推送。打 tag 发版仍由你手动控制——Prax 不替你 push。

---

## 前置

- [Getting Started](../getting-started.md) 走完（`prax --version` 能打出 0.4.0 或更高）
- 仓库是 git 仓库，**最好**用 Conventional Commits 风格（`feat:` / `fix:` / `chore:` 前缀）。不严格也能跑，但产出质量会降
- 可选：`gh` CLI 装好且登录，用来补 issue 标题；没装走降级路径也行

---

## Phase 1 · 用本仓库跑一次 dry-run（5 分钟，只读）

**Phase 1 不写任何文件**，只让 Prax 把 release notes 内容打印到终端——你可以先看质量再决定要不要落盘。

### Step 1：准备环境

直接在你要发版的仓库目录下跑：

```bash
cd /path/to/your-repo        # 换成你仓库的路径
git status                    # 应该看到 "nothing to commit, working tree clean"
```

**工作区不干净？** Prax 能跑，但为了干净产出建议先 commit 或 stash。

### Step 2：看看你最近有哪些提交

```bash
git log $(git describe --tags --abbrev=0)..HEAD --oneline --no-merges
```

**应该看到**类似（本仓库当前输出示例）：

```
20db76f docs: fix two broken cross-links + lock with regression test
421559f docs(tutorials): add phased ai-news-daily tutorial + sample vault
f32a7cb docs: add 5-minute getting-started for absolute beginners
...
60be81a feat(notify): add NotifyTool with feishu/lark/smtp providers
```

这就是 Prax 将要编译的原料。

### Step 3：dry-run 生成

```bash
prax prompt --permission-mode danger-full-access \
  "按 release-notes 技能生成 v0.4.0 的发版说明。这次只输出到终端，不要写任何文件。"
```

**为什么要 `danger-full-access`**：这个 skill 要跑 `git log v0.3.2..HEAD --format=...` 拿每条 commit 的完整 body（里面的 `#NN` issue 引用要保留到 CHANGELOG）。`git log` 走 Bash 工具，默认 `workspace-write` 模式拦掉 Bash。不给这个权限，skill 只能拿到 commit subject，拿不到 body，生成的 CHANGELOG 会漏掉 issue 引用。

**会流式输出**一段 markdown。具体措辞取决于你的 commit 历史和模型。下面是 [`examples/release-notes-demo`](../../examples/release-notes-demo/) 真跑一次得到的产出（12 条 demo commit，`gpt-5.4` 模型），**全 7 条硬契约 PASS**：

```markdown
## [0.2.0] - 2026-04-22

### Breaking
- **api**: wrapped responses in a data envelope. Refs #31.

### Added
- billing: added invoice PDF export. Refs #18.
- auth: added OAuth login support. Refs #12.

### Changed
- core: cached config parse result.
- core: extracted shared time helper.

### Fixed
- billing: fixed duplicate charges on retry. Refs #23.
- auth: fixed token refresh race. Refs #17.

### Documentation
- auth: explained the MFA flow.
- setup: added setup guide.
```

注意 `chore: bump version to 0.2.0-dev`、`test:` 和 `ci:` 提交**被正确跳过**；`BREAKING CHANGE:` 被置顶；所有 5 条 `#NN` 引用都保留在对应 bullet 末尾。想自己复现这份输出，见 [`examples/release-notes-demo/README.md`](../../examples/release-notes-demo/README.md)。

**满意？→ Phase 2（真落盘）**
**不满意？** 再跑一次，加更具体的指令：

- "按 Keep-a-Changelog 规范，breaking changes 放最前"
- "把 docs/ 相关的折叠到一行"
- "使用过去时，不要 marketing 语气"

Prax 每次会把上次的产出作为 reference 再来一遍。

### Phase 1 排错

| 症状 | 解法 |
|---|---|
| `fatal: No names found, cannot describe anything.` | 仓库还没打过任何 tag。跟 Prax 说"从 HEAD~N 开始生成"，或先 `git tag v0.1.0` 给最旧的 commit 打一个 |
| 生成的 CHANGELOG 一半英文一半中文 | Prax 跟着你的 commit message 语言走。commit 混语言导致的，下次发版前统一 |
| `chore: bump version` 这种被分进 Added | SKILL.md 里本应跳过，如果没跳，在 prompt 里明确说"chore: bump version 请跳过" |

---

## Phase 2 · 落盘（5 分钟）

### Step 1：确认输出位置

Prax 会写两个文件：

- `CHANGELOG.md`：**追加**新条目到 `## [Unreleased]` 下方，不覆盖旧版本
- `docs/releases/v0.4.0.md`：独立公告文件

如果 `docs/releases/` 不存在，Prax 会建。

### Step 2：真跑一次

```bash
prax prompt --permission-mode danger-full-access "按 release-notes 技能生成 v0.4.0 的发版说明，写到 CHANGELOG.md 和 docs/releases/v0.4.0.md"
```

**等 30 秒到 1 分钟**。完成后：

```bash
# 看 CHANGELOG 新条目
git diff CHANGELOG.md

# 看独立公告
cat docs/releases/v0.4.0.md
```

**应该看到**：CHANGELOG.md 的 `## [Unreleased]` 下方新增一整段 `## [0.4.0] - 2026-04-22 ...`；新建的 `docs/releases/v0.4.0.md` 里是完整公告。

### Step 3：幂等测试（重要！）

再跑一次完全相同的命令：

```bash
prax prompt --permission-mode danger-full-access "按 release-notes 技能生成 v0.4.0 的发版说明，写到 CHANGELOG.md 和 docs/releases/v0.4.0.md"
```

**应该观察到**：CHANGELOG.md 里 `## [0.4.0]` 段被覆盖（不是追加两份）。`docs/releases/v0.4.0.md` 被覆盖。

```bash
grep -c "^## \[0.4.0\]" CHANGELOG.md
```

**应该输出**：

```
1
```

**输出 2**？SKILL.md 的幂等逻辑没生效。检查 Prax 用的模型是否支持跟踪指令；试更强的模型。

### Step 4：发版（由你手动！）

Prax 故意**不替你打 tag、不 push、不 npm publish**。看满意了自己来：

```bash
git add CHANGELOG.md docs/releases/v0.4.0.md
git commit -m "docs: release notes for v0.4.0"
git tag v0.4.0
git push origin main --tags
# 如果是 npm 包
npm publish
```

### Phase 2 排错

| 症状 | 解法 |
|---|---|
| CHANGELOG 里出现两次同版本号段 | 幂等失败。看 Step 3 |
| `docs/releases/` 下的文件是空的 | Prax 中途出错。看 `.prax/sessions/` 下最新 session，看它执行轨迹哪一步卡了 |
| 内容和 commit 不匹配 | Prax 摘要偷懒了。用更强模型再跑：在 prompt 末尾加 ` --model claude-sonnet-4-7`（或你配的最强模型）|

---

## Phase 3 · 发版后自动群通知（10 分钟）

假设你想：打完 tag `git push` 后，自动给飞书群发一条"v0.4.0 发布啦"的卡片。

### Step 1：拿飞书 webhook

参见 [support-digest Step 8](./support-digest.md#step-8可选简报直接发到飞书群)——一模一样的拿法，只是建个**新群**（例如"产品发布通告"）拿一个新 webhook。

### Step 2：配 notify.yaml

```bash
cat > .prax/notify.yaml <<'YAML'
channels:
  release-announce:
    provider: feishu_webhook
    url: "${FEISHU_RELEASE_WEBHOOK}"
    default_title_prefix: "[Release] "
YAML

export FEISHU_RELEASE_WEBHOOK="https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxxxxxxxxxx"
```

### Step 3：在生成发版说明时一并推送

```bash
prax prompt "按 release-notes 技能生成 v0.4.0 的发版说明，写到 CHANGELOG.md 和 docs/releases/v0.4.0.md，完成后调用 Notify 工具把公告的 Highlights 段发到 release-announce channel"
```

**应该在飞书群收到**一张卡片：

```
[Release] Prax v0.4.0 released
━━━━━━━━━━━━━━━
大版本更新：新增 notify / cron / 7 个 skill 包 + 合规导向的 support-digest pipeline。

## Highlights
- NotifyTool 支持 feishu/lark/smtp
- 跨进程 cron 调度器
- 7 个 skill packs...
```

### Step 4（可选）：打 tag 后自动触发

写一个 git hook（`.git/hooks/post-receive` 或手跑的 script），收到 `v*.*.*` tag 时跑 Prax：

```bash
#!/bin/bash
# save as .git/hooks/post-tag（或者作为 CI step）
NEW_TAG=$1  # 从上层逻辑传进来
prax prompt "按 release-notes 技能生成 $NEW_TAG 的发版说明..."
```

具体 git hook 配法超出本教程——知道原理即可。

### Phase 3 排错

| 症状 | 解法 |
|---|---|
| 飞书 webhook 收不到 | 参见 [support-digest Step 8 排错](./support-digest.md#step-8可选简报直接发到飞书群) |
| 卡片标题是 `[Release] cron [...]` 而不是预期的 | prompt 里"标题"没说清。明确写"标题是 Prax <version> released" |

---

## 想自动化到什么程度

| 场景 | 推荐 |
|---|---|
| 手动触发就够，我要亲眼看一遍 | 走 Phase 1+2，跳过 Phase 3 |
| 想自动推送给团队 | Phase 1+2+3 |
| 想每天凌晨扫有无新 tag 就自动生成 | Phase 3 + cron（`0 0 * * *`），对本仓库 `git describe --tags` 的最新 tag 做增量 |

---

## 和其他 skill 的配合

- 发版前先跑 [`docs-audit`](../recipes/docs-audit.md)：确保这版修了的代码相关文档也跟上
- 发版前跑 [`pr-triage`](../recipes/pr-triage.md) 扫本次合并的所有 PR：确认没遗漏
- 发完版第二天让 [`support-digest`](./support-digest.md) 关注新增投诉 category

---

## 硬边界（Prax 不会做的事）

- 不打 tag
- 不 `git push`
- 不 `npm publish`
- 不替你从 `pyproject.toml` 读版本号再自己决定版本
- 不自作聪明把 `refactor(api)` 标成 breaking change —— 必须 commit body 里真有 `BREAKING CHANGE:` 才标

问题：<https://github.com/ChanningLua/prax-agent/issues>
