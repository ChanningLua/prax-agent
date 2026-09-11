# Prax Core Principles · 核心理念

## 人在关键处决策，Agent 在其余时间持续工作

Prax 相信，可靠的 Agent 系统既不应要求人类盯住每一步，也不应把“无人值守”误解为“无限权限”。真正可用的自治，是 **HITL（Human in the Loop）与 AFK（Away From Keyboard）的动态组合**：

```text
HITL：人定义目标、边界与验收标准
  ↓
AFK：Agent 在受控环境中执行、验证、修复和记录
  ↓
遇到高风险、歧义、证据不足或环境故障
  ↓
HITL：系统停在决策点，等待人类判断
  ↓
AFK：获得明确决定后，从持久状态继续
  ↓
HITL：人类验收结果并批准不可逆的最终动作
```

这不是两个互斥的运行模式，而是一套 **自适应自治（Adaptive Autonomy）**：低风险、可验证的工作尽量 AFK；涉及意图、偏好、金钱、合规、生产环境或不可逆副作用的决定回到 HITL。

> **Prax = Environment Runtime + Adaptive Autonomy Controller + Evidence Plane**

模型负责思考与执行；Prax 负责让状态真实、动作受控、反馈可信、过程可恢复、结果有证据。

这三个部分分别回答三个问题：

| 架构层 | 核心问题 | 职责 |
|---|---|---|
| **Environment Runtime** | Agent 在哪里工作？ | 提供真实状态、工具、权限、隔离、持久化和恢复能力 |
| **Adaptive Autonomy Controller** | 人什么时候决策？ | 根据风险、歧义、可逆性、验证强度和环境健康度，在 HITL 与 AFK 之间切换 |
| **Evidence Plane** | 结果如何被证明？ | 采集验证结果、运行轨迹和制品，区分功能失败与环境故障，并形成可审计证据 |

架构上三者相加，可靠性上三者相乘：

> **可信自治 = 真实状态 × 有界行动 × 可证结果**

任何一项为零，自治就不可信：状态不真实，Agent 会优化幻觉；行动无边界，错误会变成事故；结果不可证，“完成”就只是模型的一句话。

因此 Prax 的运行原则可以压缩成一句话：

> **HITL at decisions. AFK in execution. Evidence at every boundary.**
>
> 人在决策点介入，Agent 在边界内持续执行，每次跨越边界都留下证据。

## 三条架构不变量

这些不变量用于判断一个新功能是否符合 Prax：

1. **没有明确边界，就不授予行动权。** Agent 获得的权限必须来自环境契约，而不是自己推断。
2. **没有升级路径，就不进入 AFK。** 无法处理的风险、歧义和环境故障必须能够停靠 HITL，而不是静默猜测。
3. **没有验证证据，就不宣称完成，也不沉淀经验。** 运行日志不是证明；独立、可复现的验收结果才是。

## 六条原则

### 1. 人类拥有判断权，Agent 获得执行权

目标、范围、价值取舍和不可逆决定属于人类。搜索、修改、测试、重试和整理证据可以交给 Agent。Prax 要减少无意义的逐步确认，同时把人类注意力留给真正需要判断的节点。

### 2. 自治是一条连续谱，不是一个开关

同一项任务可以从 HITL 开始，在边界明确后进入 AFK，遇到风险或不确定性时再次停靠 HITL。自治等级必须由任务风险、可逆性、验证强度和环境健康度决定，而不是由一个全局 `auto-approve` 开关决定。

### 3. 证据优先于 Agent 的自我声明

“我完成了”不是完成。通过独立验证并留下可复现证据，才是完成。Prax 明确区分：

- `PASS`：证据证明验收条件成立；
- `FEATURE-FAIL`：功能或代码未满足验收，Agent 可以在 AFK 循环中继续修复；
- `HARNESS-ERROR`：验证环境无法说真话，系统必须停靠并升级给人，不能把环境故障教成代码经验。

### 4. 环境限制爆炸半径

安全不能只依赖人盯着屏幕。工作区边界、最小权限、隔离执行、预算、超时、审批红线和 kill switch 必须由环境强制执行。AFK 的前提不是信任 Agent 永不犯错，而是即使犯错，影响仍然可控。

### 5. 中断是正常状态，恢复必须是确定的

人可能离开，模型上下文可能重置，网络和执行器可能失败。只有写入持久状态的进度才算进度。每次运行都应能够暂停、审计、重放并从明确检查点继续，而不是依赖一次对话永远在线。

### 6. 只有经过验证的经验才能进入长期记忆

记忆不是越多越好。未经验证的推断、偶然通过的结果和环境故障不得晋升为长期经验。Prax 的自我改进必须建立在可信轨迹、独立验证和可追溯来源之上。

## 我们不追求什么

- **不是处处弹窗的审批墙**：高频低价值确认会制造审批疲劳，而不是安全。
- **不是无限循环的无人值守**：AFK 必须有目标、预算、停止条件和升级路径。
- **不是另一个绑定模型的 Agent 外壳**：执行器可以更换，环境契约、控制边界和证据应保持稳定。
- **不是用更多记忆掩盖错误反馈**：环境不能证明结果时，系统应该诚实地停下来。

## 产品承诺

Prax 的每一项能力都应回答至少一个问题：

1. Agent 看到的状态是真的吗？
2. Agent 能做什么，哪些动作必须由人决定？
3. 观察和验证证据如何返回？
4. 失败属于功能、执行器，还是环境？
5. 如何限制副作用，并在中断后安全恢复？

如果一项“自动化”无法回答这些问题，它还不是 Prax 所追求的自治。

---

## Humans decide at the boundaries; agents keep working in between

Prax combines **HITL (Human in the Loop)** and **AFK (Away From Keyboard)** as one adaptive operating model. Humans define intent, constraints, acceptance criteria, and irreversible decisions. Agents execute, test, repair, and collect evidence inside those boundaries. When risk, ambiguity, insufficient evidence, or an unhealthy environment appears, the run parks for human judgment and resumes from durable state afterward.

This is **Adaptive Autonomy**, not a choice between manual operation and unrestricted automation.

> **Prax = Environment Runtime + Adaptive Autonomy Controller + Evidence Plane**

The model reasons and acts. Prax keeps state truthful, actions bounded, feedback reliable, runs resumable, and outcomes evidence-backed.

The three layers answer three different questions:

| Layer | Core question | Responsibility |
|---|---|---|
| **Environment Runtime** | Where does the agent work? | Provides truthful state, tools, permissions, isolation, durable state, and recovery |
| **Adaptive Autonomy Controller** | When must a human decide? | Moves between HITL and AFK according to risk, ambiguity, reversibility, verification strength, and environment health |
| **Evidence Plane** | How is an outcome proved? | Collects verifier results, trajectories, and artifacts; separates feature failure from environment failure; produces auditable evidence |

The layers add architecturally, but multiply in reliability:

> **Reliable Autonomy = Truthful State × Bounded Action × Verifiable Outcome**

If any factor is zero, autonomy is not trustworthy. False state makes the agent optimize an illusion; unbounded action turns mistakes into incidents; unverifiable outcomes reduce “done” to a model claim.

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
4. **The environment caps the blast radius.** Isolation, least privilege, budgets, timeouts, approval red lines, and kill switches are enforced controls, not prompt suggestions.
5. **Interruption is normal.** Durable state, audit logs, checkpoints, and deterministic resume are part of the runtime contract.
6. **Only verified experience earns long-term memory.** Unverified conclusions and harness failures must never become durable lessons.

Prax is not an approval wall, an infinite unattended loop, or a model-specific wrapper. It is the control and evidence layer that lets agents work independently without removing human authority.
