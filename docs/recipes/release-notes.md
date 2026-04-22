# Recipe · Release Notes Generator

**目标用户**：Release manager / 技术负责人

**解决的问题**：每次发版手工从 `git log` 里挑 commit、分类、写 CHANGELOG、再写一份公告、再发群通知——半小时没了。这个 skill 把这一整串压到 30 秒。

**前置**：

- 仓库有清晰的 commit 规范（Conventional Commits 风格：`feat(xxx): ...`、`fix: ...`）
- 可选：`gh` CLI + 登录态，用来补 issue 标题。没有也能跑。
- 可选：`.prax/notify.yaml` 里有 `release-announce` channel，想推群的话。

## 一次性触发

```bash
# 版本号带不带 v 都行
prax prompt "生成 v0.4.0 的 release notes"

# 或指定上一个 tag
prax prompt "生成 v0.4.0 的 release notes，从 v0.3.2 开始对比"
```

产出：

- `CHANGELOG.md` 插入 `## [0.4.0] - 2026-04-22` 段（不覆盖既有版本）
- `docs/releases/v0.4.0.md` 公告文档（独立文件，适合粘到邮件/群里）

## 看一眼输出再发版

```bash
git diff CHANGELOG.md
cat docs/releases/v0.4.0.md
```

满意就提交：

```bash
git add CHANGELOG.md docs/releases/v0.4.0.md
git commit -m "docs: release notes for v0.4.0"
git tag v0.4.0
git push origin main --tags
```

**Prax 不替你打 tag、不替你发布**——内容生成和发布动作分开，避免"手一滑就上线"。

## 想要自动推送到群

配置 `.prax/notify.yaml`：

```yaml
channels:
  release-announce:
    provider: feishu_webhook
    url: "${FEISHU_RELEASE_WEBHOOK_URL}"
    default_title_prefix: "[Release] "
```

skill 会在写完文件后自动发一条卡片，内容是 `docs/releases/v0.4.0.md` 的 Highlights 段。

## 想要定时跑

```bash
# 每天 23:00 扫是否有新 tag，有就生成
prax cron add \
  --name release-notes-watch \
  --schedule "0 23 * * *" \
  --prompt "检查最近 24 小时内有无新 tag（git tag --sort=-creatordate | head -1 的 creatordate 是否今天），有就生成对应版本的 release notes" \
  --notify-on success \
  --notify-channel release-announce
prax cron install
```

## 幂等 — 重跑覆盖

同一版本号多次跑，会覆盖 `CHANGELOG.md` 里同版本号的旧条目，不会堆积。想重写某版只管重跑。

## 脾气

- commit message 写得越规范，生成的 CHANGELOG 就越结构化
- `chore: bump version` 这类 commit 会被跳过（这不是用户可见的改动）
- `BREAKING CHANGE:` 在 commit body 里出现 → 自动顶到最前面
- 断网 / 没 `gh` → 照样能跑，只是 issue 引用只有编号没有标题
