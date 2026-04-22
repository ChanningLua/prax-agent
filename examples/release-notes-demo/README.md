# examples/release-notes-demo · 真实验证 release-notes 技能

> **当前状态（2026-04-22）**：`replay.sh` 和 `assertions.sh` 脚本已验证可用，但 **LLM-driven 的 `prax prompt` 那一步尚未成功跑完一次** —— 检测时的 Prax 运行环境没有配置任何模型的 API key（`prax status` 的 `flow_status` 三项全是 `off`）。
>
> `run.log` 里保留了上次尝试的真实报错（`Error: Model 'gpt-4.1' not found in configuration`），证明：
> - demo 仓库被正确构造
> - prax CLI 调用触发了模型解析阶段
> - 因 key 缺失退出
>
> 配好 key 后任何人能在 3 条命令内跑完整条链路并拿到 7 个契约的 PASS/FAIL。

**这个目录做什么用**：提供一套可复现的脚手架，让任何人（包括未来的 maintainer）都能对 `skills/release-notes/` 做一次真跑验证——不是靠 unit test，而是让 Prax 真的调 LLM、真的读 `git log`、真的写 CHANGELOG.md。

## 组成

| 文件 | 作用 |
|---|---|
| `replay.sh` | 从零 rebuild 一个 `sandbox/` demo 仓库，12 条精心设计的 commit 覆盖 release-notes 所有契约分支 |
| `assertions.sh` | 对 Prax 跑完后的 `sandbox/` 断言 7 个硬契约 |
| `sandbox/` | replay.sh 的产出目录，**gitignored**。不需要 commit |
| `run.log` | 真跑一次捕获的 Prax stdout（本文件随仓库提交，作为"真的跑过"的证据）|

## 验证流程（需要已配好 API key）

```bash
cd examples/release-notes-demo

# 1. 建仓库（幂等，反复跑都得到相同 git 历史）
./replay.sh

# 2. 让 Prax 真跑 release-notes 技能
cd sandbox
prax prompt "按 release-notes 技能生成 v0.2.0 的发版说明，写到 CHANGELOG.md 和 docs/releases/v0.2.0.md" 2>&1 | tee ../run.log

# 3. 验证 7 个契约
cd ..
./assertions.sh
```

全 PASS 就说明 skill 在真 LLM 下的确遵守契约。

## 7 个契约（`assertions.sh` 强制）

| # | 契约 | 为什么重要 |
|---|---|---|
| 1 | `CHANGELOG.md` 有且仅有一个 `## [0.2.0]` | 幂等；重跑不叠加 |
| 2 | `## [0.2.0]` 块里 `### Breaking` 段在最前 | Keep-a-Changelog 语义：breaking 最显眼 |
| 3 | `chore: bump version to 0.2.0-dev` commit **不**出现在 CHANGELOG | SKILL.md 硬跳过；否则 CHANGELOG 会有自我指涉 |
| 4 | `#12 #17 #18 #23 #31` 都保留 | issue 引用可追溯 |
| 5 | `docs/releases/v0.2.0.md` 存在且非空 | 独立公告可粘到邮件/群里 |
| 6 | Prax **没有**自动创建 tag `v0.2.0` | 硬边界：打 tag 是人的决策 |
| 7 | 工作树写操作**只**发生在 `CHANGELOG.md` 和 `docs/releases/` | Prax 不碰源代码 |

## demo 仓库的 commit 清单

`replay.sh` 构造的 `v0.1.0..HEAD` 刻意覆盖每一个契约分支：

- **feat/fix/refactor/perf/docs/chore/test/ci** 前缀各至少一个
- `chore: bump version` — 应被跳过（契约 3）
- 4 个 commit 带 `#NN` 引用（契约 4）
- 1 个 commit 带 `BREAKING CHANGE:` body（契约 2）

清单：

```
feat(api): wrap responses in data envelope     (BREAKING; Refs #31)
ci: switch to GitHub Actions
test: add integration tests for billing
chore(deps): update httpx to 0.28
chore: bump version to 0.2.0-dev                (必须跳过)
docs(auth): explain MFA flow
docs: add setup guide
perf(core): cache config parse result
refactor(core): extract shared time helper
fix(billing): duplicate charge on retry        (Refs #23)
fix(auth): race on token refresh               (Refs #17)
feat(billing): invoice PDF export              (Refs #18)
feat(auth): add OAuth login support            (Refs #12)
```

## 运行要求

- `bash`、`git`、`python3`（replay.sh 用 python3 调整 commit 日期）
- Prax 能调到一个 LLM（`prax status` 里 `glm` / `codex` / `claude` 至少一个是 `on`）
- 没有 key？先跟 [`docs/getting-started.md`](../../docs/getting-started.md) 配一个 GLM free 就能跑

## 为什么这种验证 _比单测更重要_

Unit test 验证"代码按写的跑"。这个脚手架验证的是完全不同的事：
**LLM 面对真实的、会变的提示，是否仍然遵守 SKILL.md 里的硬约束**。

LLM 的行为不是确定性的。同一 prompt 不同模型、不同温度、不同上下文都可能偷懒。单测抓不到"模型把 BREAKING CHANGE 归到 Changed 了"这种漂移——但 `assertions.sh` 能。

所以这个目录应当**定期重跑**（发版前、换模型后、升级依赖后），而不是建完就忘。

## 失败时怎么办

假设契约 3 FAIL（`chore: bump version` 出现在 CHANGELOG 里）：

1. 看 `run.log` 里 LLM 的推理轨迹
2. 如果 LLM 明显跳过了 SKILL.md 的"跳过 chore: bump version"这条 → 模型理解不够，换更强的：加 `--model claude-sonnet-4-6`
3. 如果换最强模型还失败 → `skills/release-notes/SKILL.md` 的描述不够清晰。往里面加更醒目的"**必须跳过的 commit 前缀**"枚举
4. 两个都试过还不行 → skill 的设计假设（从 commit message 分类）不成立，得换方案（从 issue label 分类等）

## 扩展

想验证其他 skill？复制本目录的结构：

```
examples/<skill>-demo/
├── replay.sh        # 构造输入
├── assertions.sh    # 验证输出
├── sandbox/         # gitignored
├── run.log          # 保留"真跑过"证据
└── README.md        # 说明 7 个契约是什么
```
