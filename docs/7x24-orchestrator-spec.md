# 7×24 Orchestrator — 按 loop-engineering 方案落地

> **方案来源**：直接采用 `loop-engineering` 开源项目的既定方案，不自造框架。prax 的角色 = 他们 primitives-matrix 里的"**无 `loop-init` 支持的 CLI 宿主**"（同 Aider / Cursor / Windsurf 行）：照他们文档的**手工迁移配方**——复制他们的 `SKILL.md` + 状态/预算/日志产物，把调度映射到 prax 自己的 `cron`/`orchestrate`。

| 元信息 | 值 |
|---|---|
| 状态 | Draft v2（**推翻 v1 自造的 P0–P3，改为照抄他们的 L1→L2→L3**） |
| 分支 | `feat/7x24-orchestrator` |
| 方案出处 | `loop-engineering/docs/QUICKSTART.md`、`pattern-picker.md`、`primitives-matrix.md`、`loop-design-checklist.md` |
| 知识层 | `docs-harness` = 他们的 **Skills 原语**的一个实现（已在 aipb-chat/Frontend/ai-chat-fe 初始化） |
| 图例 | ✅ prax 现有 · 🔨 需补 · 📋 从他们 `templates/` 抄 |

---

## §1 他们的方案（照抄，QUICKSTART 五步 + 铁律）

来自 `docs/QUICKSTART.md`：

1. **按痛点选一个 pattern**（`pattern-picker.md` 决策树）。不确定 → **Daily Triage @ L1**（学状态纪律、零 auto-merge 风险，`pattern-picker.md:63-69`）。
2. **Scaffold 产物**：`STATE.md` + `LOOP.md` + `loop-budget.md` + `loop-run-log.md`（+ skills）（`QUICKSTART.md:27`）。
3. **`loop-cost` 估成本**再排期（`QUICKSTART.md:31`）。
4. **`loop-audit` 打分**（0–100）。**L3 被封顶，直到 budget + run-log + LOOP.md budget 段就位**（`pattern-picker.md:40`）。
5. **跑第一条 loop —— 只报告**。铁律：**Week one report only，不 auto-fix、不 auto-merge，先读 loop 写了什么再让它动手**（`QUICKSTART.md:9`）。loop = 跑 `loop-triage` skill → 更新 `STATE.md` → **不改代码**（`QUICKSTART.md:60`）。
6. **读输出、提交 state**——你仍是工程师（`QUICKSTART.md:97-101`）。
7. **毕业**：周一 → L1(~40+)；周二 → 加 verifier、worktree 里试一个 assisted fix(L2)；上 L3 前 → budget+run-log 填好、LOOP.md human gates、有证明过的运行（`QUICKSTART.md:105-109`）。

**关键区分（`primitives-matrix.md:8,83-85`）**：**Loop** 发现"持续性工作"（recurring triage）；**Goal**（run-until-done / `/goal`）完成"有界任务"。—— prax 现有的 `orchestrate --verify` 是 **Goal**，不是 Loop。这解释了 v1 为什么"效果差"：**我们只有 Goal 层，没有 Loop 层（triage + STATE 纪律 + report-only 爬坡）**。

---

## §2 prax ↔ 他们的原语映射（复用清单，修正版）

`primitives-matrix.md` 对"无原生调度器的 CLI 宿主"给了明确配方（Aider 行 `:107-137` + "Choosing a Tool" `:57-66`）。对号入座：

| 原语（他们） | 他们的做法 | prax 承载 | 状态 |
|---|---|---|---|
| **Scheduling** | cron/systemd/Action 定时跑一次性 session | `cron`(run_mode:orchestrate) `cron.py:98` | ✅ |
| **Run-until-done (Goal)** | `/goal` + verifier 判停 | `orchestrate --verify`（loop-until-verified） | ✅ 已有 |
| **Loop (recurring triage)** | `loop-triage` skill → `STATE.md`，不改码 | —— | 🔨 **核心缺口** |
| **Worktrees** | 每个 implementer 一个 `git worktree` | 编排路径无 isolation | 🔨（L2 才需要） |
| **Skills** | `SKILL.md` 作为只读上下文 | **docs-harness `insight`/`read`** ✅ + 他们的 `templates/SKILL.md.*` 📋 | ✅/📋 |
| **Sub-agents (maker/checker)** | implementer session 后，**另起一个只读 reviewer session 过 `git diff`** 再 commit（`:114`） | `.prax/agents/{code-reviewer,security-reviewer}` + `approval_gate` 三态 | ✅ 种子在，📋 抄 verdict 格式 |
| **State / Memory** | `STATE.md` at repo root，每轮读写同一份 | `RunJournal`（per-run，非 loop 级） | 🔨 缺 STATE.md 脊柱 |

→ **结论**：prax 不是白地，但缺的正是他们方案的**心脏**——Loop 层（`loop-triage`+`STATE.md`）+ report-only 爬坡。补法是**抄他们的产物**，不是自造。

---

## §3 落地路线 = 他们的 L1 → L2 → L3（不再 P0–P3）

严格照 `loop-design-checklist.md` 的分级 + `stories/l1-to-l2-graduation.md` 的毕业标准：

### L1 — Report-only（week 1–2）
- **做**：选首个低风险 pattern（§7 待定）；scaffold §4 产物；prax `cron` 定时跑 `loop-triage` → 写 `STATE.md`；**绝不改码/不 auto-merge**。
- **prax 工作量**：把 `loop-triage` skill 接进 `orchestrate` 的 compose（`ContextComposer` 已有注入点）；`orchestrate` 无 verifier 时本就"跑一步即停"，**这正好是 report-only 语义**（不是 bug，是 L1）。
- **毕业**：`loop-audit ~40+`；High Priority 噪声 <20%（`l1-to-l2-graduation.md:31`）。

### L2 — Assisted（week 3+）
- **做**：加 `minimal-fix` + `loop-verifier`（**独立 reviewer session**，抄 `templates/SKILL.md.verifier`）；worktree 隔离；**attempt cap ≤3 → escalate**；只小赢。
- **prax 工作量**：`orchestrate --verify` 已提供 loop-until-verified + stuck 检测（= attempt cap 雏形）；把 verifier 从"命令"扩到"reviewer sub-agent"（复用 `.prax/agents`）。
- **毕业**：verifier+worktree 在**手动**修上验证过；denylist + 测试命令入 `AGENTS.md`；`loop-audit ≥58`（`l1-to-l2-graduation.md:30-33`）。

### L3 — Unattended
- **做**：`loop-budget.md` + `loop-run-log.md` 填好；`LOOP.md` human gates；denylist；跨窗口预算 + `loop-pause-all` kill-switch。
- **前置检查**：`safety.md:87-94` Pre-Flight（denylist / auto-merge off / connector scope / human gates / kill-switch）。

---

## §4 要落的产物（照抄 `templates/`，不自造 schema）

全部从 `loop-engineering/templates/` 复制到目标仓库（§7 定位置）：

| 产物 | 作用 | 出处 |
|---|---|---|
| `LOOP.md` | active loops + cadence + gates + kill-switch + 多 loop 优先级 | `LOOP.md`（repo 根示例） |
| `STATE.md` | **High Priority(含 waiting-on-human)** + Watch + Noise + Run-log footer | `templates/STATE.md.template` |
| `loop-budget.md` | per-pattern 日上限 + on-exceed(停调度→写日志→通知) + `loop-pause-all` | `templates/loop-budget.md.template` |
| `loop-run-log.md` | append-only 8 字段 JSONL，prune 30d | `templates/loop-run-log.md.template` |
| `loop-constraints.md` | 绑定规则（push/merge、paths、code、budget） | `templates/loop-constraints.md` |
| SKILL: `loop-triage` | L1 心脏：High/Watch/Noise/State 四段，"signal not invention" | `templates/SKILL.md.loop-triage` |
| SKILL: `minimal-fix` | 最小 diff；>5 文件或需设计→停并升级；不自判 done | `templates/SKILL.md.minimal-fix` |
| SKILL: `loop-verifier` | maker/checker：默认 REJECT、自己跑测试、三态 verdict | `templates/SKILL.md.verifier` |
| SKILL: `loop-budget` / `loop-constraints` | 预算守卫 / 约束执行 | 同名 templates |

> **STATE.md 的 `High Priority (waiting on human)` 段就是"升级队列"的 L1 形态**（`STATE.md.template:5`）——先用他们这个 markdown 段，别自造 `escalation_queue.py`。等某条 pattern 毕业到 L2/L3、单文件顶不住了，再谈结构化队列（附录 A）。

---

## §5 docs-harness 的定位（收敛）

- docs-harness = **Skills 原语的实现**：`insight`/`read` 在每步给 `claude -p` 注入权威文档（偿还 intent debt）——这是它在本方案里的**主职**。
- 它的 `schedule-document-quality-maintenance` = 未来 fleet 里的**一条 pattern**（自带 `validate` 当 verifier、signals 当队列，风险低，适合当 L1 首发候选之一）。
- 它的 `signal 流 ↔ 升级队列`字段级对接（原 B 分析）= **只有当 docs-quality pattern 毕业到 L2** 才需要的细节 → 降级到**附录 A**，不进主线。

---

## §6 第一步：照 QUICKSTART 五步映射到 prax

| 他们的步骤 | prax 等价命令/动作 |
|---|---|
| 1 选 pattern | 见 §7 待确认 |
| 2 scaffold | 手工 `cp` §4 的 `templates/*` 到目标仓库（prax 无 `loop-init --tool prax`，走手工迁移配方 `primitives-matrix.md:99-103`） |
| 3 cost | `npx @cobusgreyling/loop-cost --pattern <id> --level L1`（他们的工具，直接用） |
| 4 audit | `npx @cobusgreyling/loop-audit . --suggest`（打分、看差距） |
| 5 run report-only | `prax cron add`（run_mode:orchestrate）定时跑："Run loop-triage. Read STATE.md. Update High Priority/Watch/Noise. **Do not edit code.**" |
| 6 read/commit | 人读 `STATE.md`，提交 scaffold + 首轮更新 |

---

## §7 待确认决策

1. **首个 pattern**：
   - (a) **Daily Triage @ L1**（他们的默认推荐，学纪律、最低风险，`pattern-picker.md:63`）
   - (b) **docs-quality triage @ L1**（贴合 aipb-chat 现有 docs-harness，自带 `validate` verifier）
2. **产物落哪**：prax 仓库（练方案）还是 aipb-chat（真业务库）？scaffold 目标目录？
3. **是否直接用他们的 npm 工具**（`loop-init`/`loop-audit`/`loop-cost`/`loop-context`）打分/估算/熔断，还是逐步 port 进 prax？（建议：**先直接用**，符合"按他们方案来"；port 是后话。）
4. **手工迁移的 tool 目标**：他们 `loop-init --tool` 只有 grok/claude/codex/opencode。prax 走"复制 SKILL+STATE、映射调度"的手工路（`primitives-matrix.md:57-66`）——确认这条路。

---

## 附录 A — docs-harness signal ↔ 结构化升级队列（仅当 docs-quality pattern 到 L2 才启用）

保留原 B 分析的可执行结论，供后续取用：
- **source→projection**：`signal list --unhandled --since <lastRun>` 投影进队列；`mark-handled` 是 close 边（幂等，`signals.ts:91-127`）。
- **风险分档表**（⚠️待确认）：`empty_route`/`readme_unindexed`/`route_missing_readme_entry`/`route_without_readme` → L2 自动修；`non_target_document`/`read_unindexed_target`/`read_unreachable_target`/意图漂移 → 升级人。
- **坑**：signals 由 detached worker 异步写、落本地日期文件夹（`signals.ts:246-251`）→ 轮询别假设同步；handled 信号重测会追加新 unhandled 行（reopen，`signals.ts:184-200`）。

## 附录 B — C 抄清单（映射到 §3 各级，随 pattern 毕业逐步落）

- **maker/checker（→L2）**：抄 `SKILL.md.verifier` verdict 三态 + 默认 REJECT + 自跑测试；prax 复用 `.prax/agents/code-reviewer`、`approval_gate` 三态。
- **控制面（→L3）**：抄 `loop-budget.md`(caps/80%→report-only/100%→退) + `loop-run-log.md`(8 字段) + `loop-pause-all`；prax 复用 `claude_step_executor` 的 per-step `cost_usd`（已采未累计）+ `governance` budget 语义。
- **机队/multi-loop（→后置）**：抄 `multi-loop.md` 优先级表 + `acting_on` 撞车检测 + 每 pattern 一状态文件。
</content>
