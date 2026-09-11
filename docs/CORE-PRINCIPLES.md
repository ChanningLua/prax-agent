# Prax Core Principles · 核心理念

Prax 希望让人放心地把工作交给 Agent：事先说清目标和权限，执行时有约束，结束后能检查结果。人不必盯住每一步，也不必为了省事把所有权限一次性交出去。

这份文档说明我们为什么这样设计、已经做到了什么，以及接下来要补什么。具体使用规则见 [Repository Harness](repository-harness.md)、[Shift](prax-shift-spec.md) 和[一次性验证](verification-tasks.md)；原生 Agent、技能和旧版兼容入口并不具备完全相同的控制能力。

## 人在关键处决策，Agent 在其余时间持续工作

Prax 把 **HITL（Human in the Loop，人在关键环节参与决策）** 与 **AFK（Away From Keyboard，人可以暂时离开键盘）** 结合起来。该由人决定的事交给人，目标和权限已经明确的工作交给 Agent：

```text
HITL：人确定目标、权限与验收标准
  ↓
AFK：Agent 按约定执行、验证，并记录结果
  ↓
遇到高风险、需求不明确、证据不足或环境故障
  ↓
HITL：暂停工作，把问题交给人判断
  ↓
AFK：获得明确决定，并确认可以恢复后，按记录继续
  ↓
HITL：人验收结果，并决定是否执行不可逆操作
```

这不是让用户在“全手动”和“全自动”之间二选一，而是让 Agent 的自主程度随任务变化。我们称之为 **自适应自治（Adaptive Autonomy）**：低风险、容易验证的工作尽量连续执行；涉及需求取舍、个人偏好、金钱、合规、生产环境或不可逆操作时，先请人决定。

> **Prax = Environment Runtime + Adaptive Autonomy Controller + Evidence Plane**

模型负责理解任务和执行操作；Prax 要把执行环境、继续或停止的规则，以及结果的验证方式组织起来。

这三个部分描述的是 Prax 的整体设计，不是三个可以分别部署的产品。Prax Shift 是其中的持续工作模式；原生 Agent、技能扩展、记忆组件，以及知识整理和内容生产流程，也都是 Prax 的一部分。

这三个部分分别回答三个问题：

| 架构层 | 核心问题 | 职责 |
|---|---|---|
| **Environment Runtime · 环境运行时** | Agent 在什么环境里工作？ | 管理工作状态、工具和权限，提供隔离、记录与恢复能力 |
| **Adaptive Autonomy Controller · 自适应自治控制器** | 什么时候继续，什么时候请人决定？ | 结合风险、需求是否明确、操作能否撤回、验证是否充分，以及环境是否正常，决定下一步 |
| **Evidence Plane · 证据层** | 凭什么认为工作完成了？ | 保存验证结果、执行过程和交付文件，区分代码问题与环境问题，让结果可检查、可追溯 |

三个部分缺一不可。我们用另一条公式提醒自己检查可靠性：

> **可信自治 = 真实状态 × 有界行动 × 可证结果**

看到的状态不真实，Agent 就可能沿着错误方向继续做；操作没有限制，一次失误就可能影响整个项目；结果无法验证，“完成”就只是一句话。

这不是用于计算可靠性的统计公式，而是一份设计检查清单：三项都要成立，不能靠其中一项做得好来弥补另一项完全缺失。

用一句话概括：

> **HITL at decisions. AFK in execution. Evidence at every boundary.**
>
> 关键决定由人来做，Agent 按约定持续执行，交接时说清做了什么、依据是什么。

## 三条设计底线

每项新能力都应守住这三条底线：

1. **先约定范围，再开始执行。** Agent 的权限来自明确的任务约定，不能靠自己猜测。
2. **遇到无法判断的问题，必须能停下来。** 风险、需求疑问和环境故障要交还给人处理，不能为了继续运行而擅自作主。
3. **没有验证，就不把结果当成事实。** 运行日志只能说明发生了什么；完成声明和可信经验，都需要独立、可复现的验收结果支持。

## 六条原则

### 1. 人决定要做什么，Agent 负责具体执行

目标、范围、方案取舍和不可逆操作由人决定。查资料、改代码、跑测试，以及在授权范围内重试和整理证据，可以交给 Agent。Prax 要减少没有必要的逐步确认，让人把注意力留给真正重要的决定。

### 2. 自主程度随任务变化，不靠一个开关决定

同一项任务可以先由人确认，随后交给 Agent 连续执行，遇到新问题再请人判断。能放手到什么程度，要看风险有多大、操作能否撤回、结果能否验证，以及执行环境是否正常，而不是打开一个全局 `auto-approve` 就放行所有操作。

### 3. 看验收结果，不只听 Agent 说完成了

Agent 说“我完成了”，还需要验收来确认。Prax 要留下可检查的证据，并说明结果适用于哪些验收条件。仓库编码任务区分：

- `PASS`：声明的验收条件已经通过；
- `FEATURE-FAIL`：功能或代码没有通过验收，可以在约定的范围和预算内继续修复；
- `HARNESS-ERROR`：执行或验证环境出了问题，应停止并请人处理，不能把它当作业务代码错误继续修。

一次性验证还会返回 `BLOCKED`，分别记录哪些用例通过、失败、被阻塞或没有执行；主动暂停会记录为 `PAUSED`。当前覆盖情况保持 `unknown`：跑过选定用例，不等于覆盖全部业务。不同结果要分别处理，不能一律靠改代码、重跑来解决。

### 4. 用执行约束控制错误的影响范围

安全不能只靠人盯着屏幕，也不能只写在提示词里。权限、隔离、预算、超时和审批要求，最终都应落实到执行机制中。

目前 Prax 已有预算控制、进程监督和协作式仓库检查。但检查到异常时，不该发生的改动可能已经发生；测试和适配器也仍需由使用者信任。Prax 不是操作系统沙箱，没有全局紧急停止开关，也不能用这些检查替代真正的隔离。

### 5. 工作可以中断，但不能凭猜测恢复

人会离开，模型上下文会重置，网络和执行器也可能出错。下一次继续工作时，应依据保存的任务和执行记录，而不是指望一段对话永远保持完整。

编码任务恢复前，必须核对任务约定，确认工作区与受保护的检查点一致，并继续使用原来的累计预算。并非所有崩溃都能无损恢复；暂停也不会撤销已有改动。一次性验证则不允许恢复或自动重跑。

### 6. 记住经验之前，先确认它是否可靠

记忆不是越多越好。未经验证的推断、偶然通过的测试，以及被误判成代码问题的环境故障，都不应该成为下次工作的可靠依据。

这是 Prax 对记忆能力的设计要求。目前已有记忆、纠错和经验存储组件，但还没有在所有入口统一做到“先验证，再保留为可信经验”。保存了一条记录，不等于系统已经学会了正确做法，更不等于训练了模型。

## 当前实现与演进方向

| 当前已有的能力 | 接下来要完善的部分 |
|---|---|
| 按任务契约运行的 Shift 与 harness 共用运行时，可调用 Claude/Codex worker | 在真实账户、断网、额度耗尽和长期调度等条件下，建立可重复的验收流程 |
| 工作范围、仓库身份、累计预算，以及暂停和恢复检查 | 完善与编码宿主的审批交接，提供适用于更多平台的隔离方式 |
| 一次性验证的执行身份、结果和报告文件校验 | 接入各仓库的授权规则，核实报告内容，并盘点用例和业务覆盖情况 |
| 会话记录、记忆组件、可复用技能和内容质量检查 | 验证哪些经验值得保留、哪些应当清理，并通过回归评估检查效果 |
| 按配置运行周期任务并发送通知 | 建立可独立验收的新任务生成机制 |

当前自治控制依靠明确规则，不会自动学出一套风险策略或改写自身规则。契约式 Shift 调度只继续已有任务，不增加预算；原生 prompt 调度仍可周期执行已配置的提示词。

我们更愿意先把一个小流程做好：输入可靠、权限明确、验收有效，出了问题知道该交给谁。之后再扩大 Agent 可以自主完成的工作范围。Environment 并不是换个名字就宣布 Harness 过时，而是要求运行框架在真实项目中经得起检验。

## 我们不追求什么

- **不靠频繁弹窗制造安全感**：确认太多、价值太低，人反而容易忽略真正重要的决定。
- **不追求永不停下的无人值守**：持续工作必须有目标、预算、停止条件和人工接手的办法。
- **不把产品绑定在某一个模型上**：执行器可以更换，任务约定、权限控制和验收依据不应随之丢失。
- **不靠堆积记忆掩盖错误**：无法确认结果时，就应明确说明问题并停下来。

## 判断一项能力是否值得做

Prax 的每一项能力，都应帮助回答至少一个问题：

1. Agent 看到的是项目的真实状态吗？
2. 哪些事可以直接做，哪些必须先问人？
3. 做过什么、结果如何、验证依据是什么，能查清楚吗？
4. 失败是代码问题、执行器问题，还是环境问题？
5. 出错时如何控制影响，中断后又该怎样继续？

如果一项自动化只是让 Agent 跑得更久，却不能帮助回答这些问题，它就不是 Prax 优先要做的事。

---

## Humans decide at the boundaries; agents keep working in between

These are design principles, not a claim that every Prax entry point already implements the same controls. The [repository runtime contract](repository-harness.md) and [one-shot verification protocol](verification-tasks.md) define the implemented guarantees. Shift is one operating mode alongside the native agent, skills, memory components, and content workflows.

Prax combines **HITL (Human in the Loop)** and **AFK (Away From Keyboard)** as one adaptive operating model. Humans define intent, constraints, acceptance criteria, and irreversible decisions. Agents execute, test, repair, and collect evidence inside those boundaries. When risk, ambiguity, insufficient evidence, or an unhealthy environment appears, the run parks for human judgment and resumes from durable state afterward.

This is **Adaptive Autonomy**, not a choice between manual operation and unrestricted automation.

> **Prax = Environment Runtime + Adaptive Autonomy Controller + Evidence Plane**

The model reasons and acts. Prax keeps state truthful, actions bounded, feedback reliable, runs resumable, and outcomes evidence-backed.

The formula is an architectural direction, not three independently deployable components. The current controller follows explicit rules; it is not a learned risk policy or a universal approval platform.

The three layers answer three different questions:

| Layer | Core question | Responsibility |
|---|---|---|
| **Environment Runtime** | Where does the agent work? | Provides truthful state, tools, permissions, isolation, durable state, and recovery |
| **Adaptive Autonomy Controller** | When must a human decide? | Moves between HITL and AFK according to risk, ambiguity, reversibility, verification strength, and environment health |
| **Evidence Plane** | How is an outcome proved? | Collects verifier results, trajectories, and artifacts; separates feature failure from environment failure; produces auditable evidence |

The layers add architecturally, but multiply in reliability:

> **Reliable Autonomy = Truthful State × Bounded Action × Verifiable Outcome**

If any factor is zero, autonomy is not trustworthy. False state makes the agent optimize an illusion; unbounded action turns mistakes into incidents; unverifiable outcomes reduce “done” to a model claim.

This is a design heuristic, not a statistical reliability equation.

The operating principle is therefore:

> **HITL at decisions. AFK in execution. Evidence at every boundary.**

### Three architectural invariants

1. **No action authority without an explicit boundary.** Permissions come from the environment contract, never from the agent's own inference.
2. **No AFK execution without an escalation path.** Risk, ambiguity, and environment failure must be able to park for HITL instead of being silently guessed through.
3. **No completion claim or durable learning without verification evidence.** A run log is not proof; an independent, reproducible acceptance result is.

### The principles

1. **Humans own judgment; agents receive execution authority.** Intent, trade-offs, scope, and irreversible decisions remain human responsibilities.
2. **Autonomy is a spectrum, not a switch.** A run moves between HITL and AFK according to risk, reversibility, verification strength, and environment health.
3. **Evidence outranks self-report.** “Done” is a claim until an independent verifier produces reproducible evidence.
4. **Make boundaries executable.** Budgets, supervision, and cooperative repository checks are implemented; they are not an OS sandbox, rollback system, or global kill switch. Tests and adapters remain trusted code.
5. **Interruption is normal.** Coding-task continuation requires a matching guarded checkpoint and preserves cumulative budgets. Not every interruption is recoverable; one-shot verification cannot resume or automatically replay.
6. **Only verified experience should earn trust.** This is the design target. Existing memory and experience components are not yet uniformly evidence-gated across all entry points; persistence is not verified learning or model training.

### What to build next

Priorities are repeatable real-host acceptance, stronger approval handoffs and isolation, project-specific evidence bridges, and tested memory promotion and pruning. Contract-backed Shift scheduling continues a known contract; native prompt jobs can still repeat a configured prompt. Automatic creation of new daily task contracts remains separate work. Harness engineering remains part of this environment, not something a new name makes obsolete.

Prax is not an approval wall, an infinite unattended loop, or a model-specific wrapper. It is the control and evidence layer that lets agents work independently without removing human authority.
