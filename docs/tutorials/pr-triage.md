# 傻瓜式教程 · PR Triage Bot

**面向**：第一次想让 AI 做 PR review 但又不信任纯 LLM 评论的 eng lead。

**你是谁**：你带一个 5-15 人的工程小组。每天 5-10 个 PR 过来，你 deep-review 不过来。市面上那些 "AI reviewer" 工具你都试过——要么瞎猜，要么提一堆"建议加注释"废话。你想要的是**真跑测试、真对比 base、真看依赖变更**，给你一份可信的 triage 清单。

**Prax 的差异化**：它**真的 checkout 出来、真的跑 pytest**。不是文本推测，是跑完结果说话。

**完成后你会拥有**：一条命令对任意 PR 跑完整 triage —— 分类、风险评分、PR 分支 vs base 分支的测试对比、新增依赖列表。可选自动每天扫 open PRs。**所有 GitHub 写操作（approve / merge / close）都**不会**自动发生**——Prax 故意不碰这些。

---

## 前置

- [Getting Started](../getting-started.md) 走完
- 目标仓库能本地 `git clone` + 能跑测试（pytest / npm test / cargo test / go test 任一）
- 推荐：`gh` CLI 装好 + `gh auth status` 绿。**没装也能跑降级路径**（见下方 Phase 1 Step 5）

---

## Phase 1 · 对一个真实 PR 跑一次（10 分钟）

### Step 1：找个练手 PR

挑个你仓库里的真实 PR——**open 状态、带测试修改、patch 不要太大**（≤ 500 行最佳）。如果没有，挑本 Prax 仓库的一个旧 PR 试：

```bash
cd /path/to/prax-agent   # 或你自己的仓库
gh pr list --state all --limit 20
```

**应该看到**类似：

```
#42  feat(notify): add NotifyTool    OPEN    foo:notify-tool
#38  fix(auth): cookie race          MERGED  bar:hotfix
...
```

记下一个 PR 号（例如 `#42`）。

### Step 2：让 Prax triage

```bash
prax prompt "触发 pr-triage 技能，对 PR #42 做完整 triage"
```

**Prax 会做 8 件事**（按顺序）：

```
1. gh pr view #42 拿 metadata
2. gh pr diff #42 拿 patch 内容
3. 分类（feature/bug/refactor/...）
4. 风险评分（基于 patch 大小 / 依赖 / auth 路径等）
5. git fetch origin pull/42/head
6. git checkout pr-42 → VerifyCommand pytest → 记录结果
7. git checkout <base> → VerifyCommand pytest → 对比
8. git checkout - && git branch -D pr-42（必须清理！）
9. 写报告 .prax/pr-triage/pr-42-<ts>.md
10. 可选：Notify eng-leads channel
```

**全流程 3-10 分钟**（看你测试跑多久）。

### Step 3：看报告

```bash
cat .prax/pr-triage/pr-42-*.md
```

**应该看到**：

```markdown
---
pr: 42
title: feat(notify): add NotifyTool
author: @foo
stats: "+320, -15, 4 files"
---

# PR #42 Triage

## 分类
- 主：feature
- 副：docs

## 风险评分：3（中）
触发规则：
- patch = 305 行 (+1)
- 新增依赖：httpx (+2)

## 测试结果
- PR 分支 pytest -q：✅ 1929 passed
- 基线 main pytest -q：✅ 1922 passed
- **结论**：PR 引入 7 个新通过的测试，无回归

## 新增依赖
- Python: httpx (已在 dep)

## 审查要点
1. tools/notify.py:38 Provider 基类 — 检查扩展点
2. tools/notify.py:105 SMTP 密码从 env 读 — 确认无 YAML 硬编码
3. tests/unit/test_notify.py 23 个测试 — 覆盖所有分支

## 推荐动作
- [ ] 审 tools/notify.py:__init__ 接口稳定性
- [ ] 确认 notify.yaml schema 文档已更新
```

### Step 4：解读测试对比

关键是**测试对比表**：

| PR 分支 | base 分支 | 结论 |
|---|---|---|
| ✅ 通过 | ✅ 通过 | **最健康**。PR 无回归，可以安心审 |
| ❌ 失败 | ✅ 通过 | **PR 引入了问题**。明确。Comment PR 要求修 |
| ✅ 通过 | ❌ 失败 | **PR 可能在修 baseline**（或巧合绕过） |
| ❌ 失败 | ❌ 失败 | **基线坏**。PR 的问题暂时说不清，先修 main 分支 |

**Step 4 的价值**：你眼睛过一遍这张表，就知道这个 PR 是不是值得花时间 review。

### Step 5：没 `gh` CLI？走降级

```bash
gh --version
```

**`command not found`**？Prax 会自动降级到只 `git fetch origin pull/42/head` + 本地 `git diff`。报告末尾会标 `mode: degraded (gh unavailable, tests skipped)` —— 分类仍然出，测试那步跳过。

想装 `gh`（推荐）：参见 <https://cli.github.com/>，装完 `gh auth login`。

### Phase 1 排错

| 症状 | 解法 |
|---|---|
| `fatal: couldn't find remote ref refs/pull/42/head` | PR 号错了，或当前 remote 不是原仓库（是你 fork 的）。`git remote -v` 看，`cd` 到原仓库 clone |
| `git checkout pr-42` 前后工作区被污染 | skill 清理失败。手跑 `git checkout -`、`git branch -D pr-42`、`git stash` 恢复 |
| 测试跑 30 分钟还不停 | `VerifyCommand` 有 5 分钟 timeout；超了应自动停。如果卡着不动，Ctrl+C 重跑并缩小范围（比如只跑部分测试） |
| 报告说 "PR too large (2048 lines), skipping tests" | skill 的硬边界——patch > 2000 行不跑测试。告 PR 作者拆小 |

---

## Phase 2 · 每天早上自动扫所有 open PR（15 分钟）

### Step 1：先想清楚你要的频度

有的团队 PR 不多，每天扫一次够用。有的团队有 CI bot，就没必要 Prax 再扫一遍。

**适用场景**：5-15 人组，PR 日均 5-10 个，你是 lead 想要一个"昨晚这些 PR 质量如何"的视图。

### Step 2：cron 配置

```bash
prax cron add \
  --name pr-triage-daily \
  --schedule "30 8 * * 1-5" \
  --prompt "对所有 open 状态、过去 24 小时有新 commit 的 PR 逐个 triage，报告写 .prax/pr-triage/ 下，最后调 Notify 把 triage 结果发到 eng-leads channel" \
  --session-id cron-pr-triage \
  --notify-on failure \
  --notify-channel eng-leads
prax cron install
```

每周一到周五 8:30 跑。`notify-on failure` 表示只有 Prax 本身跑崩才打扰你（正常 triage 结果另外推送）。

### Step 3：配 eng-leads 飞书通道

参见 [support-digest Step 8](./support-digest.md)，建群 → 拿 webhook → 写 `.prax/notify.yaml` → 测。用名字 `eng-leads`。

### Step 4：验证

隔天早晨应当：

```bash
ls .prax/pr-triage/
```

**应该看到**当天的一批 triage 报告：

```
pr-42-20260423-083012.log
pr-43-20260423-083512.log
pr-45-20260423-084015.log
```

飞书"eng-leads"群应当每条 triage 一张卡片（或汇总成一条长卡片，看你 prompt 怎么写的）。

### Phase 2 排错

| 症状 | 解法 |
|---|---|
| cron 装了但没跑 | 参见 [ai-news-daily Phase 3 排错](./ai-news-daily.md#phase-3-排错) |
| 扫到的 PR 为空 | 当天可能真没新 PR。`gh pr list --state open` 看看 |
| 有 PR 但 Prax 跳过不 triage | 看日志里的 prompt 里面可能过滤条件太严。放宽："对所有 open 状态的 PR 逐个 triage"（去掉时间窗条件） |

---

## Phase 3 · 进阶：从 triage 报告往回修（选读）

这不是新步骤，而是**怎么用 triage 报告提升团队水平**。

### 怎么把 triage 变成教练工具

1. **测试失败的 PR → 教作者写小 PR**：连续 3 次看到同一人 PR 带 regression，triage 里的"风险规则"可以成为 1:1 材料——"你上周这 3 个 PR 都改了 auth，咱们下次分开提"
2. **所有 PR 都标 "patch > 500 行"**：团队 PR 文化问题，大家都在堆大 PR；团体 retro 上拿来讨论
3. **"没伴随测试" 累计率 > 50%**：团队测试习惯问题，考虑加入 PR template 要求 checklist

triage 报告每份都留着，累计起来就是团队代码质量趋势。

---

## 和其他 skill 的配合

- triage 扫出 "docs 没更"的 PR → 用 [docs-audit](./docs-audit.md) 补那份 drift 清单
- 发版前对最近 N 个已合并 PR 跑 triage → triage 结果作为 [release-notes](./release-notes.md) 的分类原料
- triage 发现性能回归 → 可以进一步触发自定义 benchmark skill（本教程不涵盖）

---

## 硬边界（Prax 不会做的事）

- 不 **approve** / **merge** / **close** / **comment** 到 GitHub
- 不 **force push**、不 **reset hard**、不动 `main` 分支
- **patch > 2000 行**：直接拒测，报告标"过大建议拆分"，不勉强跑
- **跑完测试必须**清理临时 branch 和 worktree 状态——你的工作区保持干净

问题：<https://github.com/ChanningLua/prax-agent/issues>
