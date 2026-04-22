# Recipe · Docs Freshness Audit

**目标用户**：DevEx、技术写作、文档负责人

**解决的问题**：代码一直在改，文档没人盯。3 个月后发现新人按文档配一遍 Prax 直接配错，你花一下午 debug——根因是文档说的是 2 个月前的行为。这个 skill 每周扫一次 drift。

**前置**：仓库是 git 仓库。`gh` CLI 可选（想开 issue 才需要）。

## 一次性跑

```bash
prax prompt "扫一下过去 30 天的文档 freshness"
```

产出：`.prax/reports/docs-audit-<YYYY-MM-DD>.md`，里面按 🔴/🟡 两档列出 drift，每项带 git log 证据。

## 每周自动扫

```bash
prax cron add \
  --name docs-audit-weekly \
  --schedule "0 9 * * 1" \
  --prompt "触发 docs-audit 技能，窗口 7 天" \
  --session-id cron-docs-audit \
  --notify-on success \
  --notify-channel devex
prax cron install
```

周一早上 9 点跑，发一条飞书到 `devex` 群。

## 想自动开 issue

创建 `.prax/docs-audit.yaml`：

```yaml
window_days: 30
auto_issue: true
notify_channel: devex
```

下次再触发时会把报告作为 issue body 开一个 `docs,maintenance` 标签的 issue，适合当 triage backlog。

## 看懂报告

- **🔴 高优先级**：源文件改了，docs 里**提到**这个文件/模块，**但** docs 没一起改。几乎一定是 drift。
- **🟡 低优先级**：源文件改了，但 docs 从未提及。可能是内部实现（不用写文档），也可能是该补文档。酌情。
- **统计**：帮你判断本次 drift 密度。如果 stale_count 持续上升，说明 doc 节奏落后了。

每个 drift 项都有 3 行 `git log`——不用自己再查。

## 和 release-notes 配合

发版前先跑一次 docs-audit，看看有没有"代码改了但文档没跟上"的东西。把 drift fix 塞进 release notes 的 `## Documentation` 段，发版说明就完整了。

## 不会做的事

- 不改文档
- 不删历史报告
- 不扫 `node_modules`、`dist`、`__pycache__`、`.venv`
- 不对"生成文件"（`*.lock`）报 stale
