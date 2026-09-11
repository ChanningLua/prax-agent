# Prax 整合与时效性审视 · 2026-09-10

> 本报告保留首次整合后的问题快照。用户随后授权的闭环优化见
> [后续实现与验收记录](2026-09-10-shift-closed-loop.md)，不要把下面的
> “当前实现”描述当作后续修复后的状态。

## 结论

Prax 的核心理念没有因为 Environment、SDK 或宿主原生自动化的发展而失效。
`Environment Runtime + Adaptive Autonomy Controller + Evidence Plane` 仍是合理的产品分工。
但当前实现不能等同于已经完成可靠的 7×24 无人值守：Shift、cron 与新 repository harness 的控制边界尚未统一。

建议保留现有资产，优先统一控制与证据契约，而不是重写一个新的 coding agent，或仅更换术语。
下述问题属于审视发现，本次没有擅自实施架构升级或启用无人值守任务。

## 整合范围与 Git 状态

- 来源：本机 `Documents/prax 2/remote-prax`，HEAD 为 `f1eefab`，包含未提交开发。
- 目标：本机 `Desktop/code/github/prax-agent`。
- 当前整合分支：`codex/integrate-local-prax`，基于 `origin/feat/7x24-orchestrator` 的 `bf4a3b3`。
- GitHub 功能分支比 `main` 的 `57f7e20` **领先 22 个提交**。此前对 `0 / 22` 的方向解释错误，已更正。
- 保留功能分支提交历史、风险审批、三态验证，并合入本机源码、测试、文档、插件资源及两个小型示例。
- 将旧 `docs/7x24-orchestrator-spec.md` 的内容合入已有的新名称 `docs/prax-shift-spec.md`，没有丢失风险加固章节。
- 补充精确 `.gitignore` 例外：Codex 插件的 `.codex-plugin/plugin.json` 不再被通用 `plugin.json` 规则遗漏。
- 本次未搬运三个日期型 article-batch 实验目录。其中包括历史运行产物和配套制图、文章构建脚本；它们仍完整保留在来源目录，不属于本次核心运行时代码整合。
- 未修改来源目录、未提交、未推送；目标 `main` 分支指针不变。整合改动目前位于目标工作区。

内容核对：纳入范围的 465 个现存文件与来源逐字节相同，无缺失；另一个文件 `.gitignore` 仅多出上述必需资源例外。本报告为整合后新增。

## 主要发现

### P1：cron → Shift 尚未形成可验证、可恢复的持续执行

[`_argv_for_job`](../../src/prax/commands/cron.py) 目前只传 goal、model 和 agent host，没有传固定 run ID、verifier 或 feature-mode 参数。
[`handle_orchestrate`](../../src/prax/commands/orchestrate.py) 在没有 run ID 时生成随机 ID；没有 verifier 时只运行一步并返回 `completed_no_verify`。
因此现有 cron 配置不是“同一个任务自动接班，直到验收通过”。把 CLI 参数写进 prompt 也只是目标文本，不会变成独立 argv。

另一个相关问题：[`main.py`](../../src/prax/main.py) 的 Shift 分支打印 outcome 后直接返回，而 cron 按进程退出码判断 success/failure。`max_iterations`、`harness_error` 等未验证结果可能仍被调度器记为 success。

修复方向：调度器持久化 task/run identity，显式保存验收和预算，区分周期巡检与有界任务续跑，传递结构化结果而非只认退出码。

### P1：旧 Shift 的恢复证据没有绑定当前任务与代码身份

[`OrchestratorLoop.run`](../../src/prax/core/orchestrator_loop.py) 发现 journal 中旧 outcome 为 verified 就立即返回成功，发生在执行器和 verifier 之前；这里不核对当前 goal、HEAD、工作区或验收定义。

本次用内存桩复现：复用一个 verified journal，传入完全不同的 goal，结果仍为 `verified=True`，且 verifier 不执行。
这是旧 Shift 路径的限制，不能泛化为新 repository harness 也没有身份保护。

修复方向：复用 [`harness_contract.py`](../../src/prax/harness_contract.py) 的任务与源代码身份契约；发生漂移时拒绝复用旧结果，要求明确新任务或重新验收。

### P1：Claude 与 Codex 的默认权限并不等价

[`ClaudeStepExecutor`](../../src/prax/core/claude_step_executor.py) 默认 `permission_mode="bypassPermissions"`；[`CodexStepExecutor`](../../src/prax/core/codex_step_executor.py) 默认 `workspace-write`。
[`agent_host.py`](../../src/prax/core/agent_host.py) 的 auto 又优先选择 Claude。更换或安装宿主后，执行权限可能随之改变。
功能级 approval relay 不能代替工作区隔离、工具权限或运行中副作用控制。

修复方向：任务先声明权限契约，再由适配器证明能满足；不满足则停下。不要把提高权限作为无人值守的默认补丁。

### P1：Shift 的轮次上限不是硬超时或仓库互斥

[`CodexCliExecutor`](../../src/prax/core/codex_cli_executor.py) 等待 `proc.communicate()`，旧 Shift 本身没有与之配套的墙钟超时和进程组监督。
一个 worker 长时间不返回时，`max_iterations` 不会生效。cron runner 同样没有超时或同仓库执行锁。

新 [`repository_harness.py`](../../src/prax/repository_harness.py) 已实现仓库锁、监督进程组、持久预算和边界检查，值得复用。
优先将 Shift 接到同一受控执行内核，而不是在两个入口分别维护不同的安全语义。

### P2：发布和测试工程存在老化

- [`package.json`](../../package.json) 仍为 `0.6.0`，[`pyproject.toml`](../../pyproject.toml) 为 `1.0.0`，需要明确统一或独立版本策略。
- 可选 LangGraph、BeautifulSoup、Textual 的缺席暴露了旧测试的 mock/依赖假设；这些问题在未整合的 `bf4a3b3` 快照同样复现。
- 文档应区分已实现能力、默认行为和演进目标，尤其不能把 repository harness 的保证直接当作 Shift/cron 的保证。
- 文章生产能力可以作为插件/配方保留，不宜继续扩展为核心控制内核的一部分。

## 哪些接口没有过时

按本机 `codex-cli 0.153.4` 的帮助核对，适配器使用的 `exec --json`、`--sandbox`、`--cd`、`--color` 等选项仍存在。
官方也仍将 `codex exec` 用于脚本、CI 和预设权限的自动化场景，因此不能只因出现新接口就判定现有 CLI 适配需要重写。[OpenAI 官方非交互模式文档](https://learn.chatgpt.com/docs/non-interactive-mode)

如果之后需要双向审批、会话控制和实时事件，App Server 是可以评估的更深集成接口；它不是本次合并的必要前提。[OpenAI 官方 App Server 文档](https://learn.chatgpt.com/docs/app-server)

本机 PATH 中未找到 Claude CLI，因此没有宣称验证过当前 Claude 版本的真实任务兼容性。
本次没有启动真实模型 worker、修改账户、安装插件、注册计划任务或访问生产系统。

## 验证记录

- `git diff --check` 通过，未残留合并冲突标记。
- 导入路径核对确认测试加载目标仓库的 `src/prax`，不是来源 editable install 的代码。
- 单元测试及两个入口/安装测试：2416 passed，7 failed。
- 另行运行集成测试及其余顶层测试：172 passed。
- 上述一个失败是子进程 `python3` 命中了没有 pytest 的 Xcode Python。仅在测试进程 PATH 中选择已有虚拟环境后，该验收命令测试通过；没有修改系统 Python 或全局 PATH。
- 其余 6 个失败在未整合的 `bf4a3b3` 快照复现：4 个 LangGraph mock 问题、1 个 BeautifulSoup 符号问题、1 个缺失 Textual 的 TUI 测试。
- 关键路径定向回归：271 passed（98.46 秒），覆盖 Claude/Codex 宿主选择、Codex 执行/安装、Shift、风险审批、三态验证、cron、repository harness 及验收命令。与首轮测试有重叠，不能相加当作独立测试总数。
- 不能将这些定向通过表述为全量测试全绿；上述可选依赖/旧测试问题仍需单独整理。

## 建议的下一轮迭代

1. 统一 task identity、验收、权限、预算和证据契约；使 Shift 与 repository harness 共用执行边界。
2. 补齐 cron 的持久任务身份、结果语义、同仓库互斥及暂停/恢复测试。
3. 为 Claude/Codex 适配器增加版本能力探测和真实协议契约测试，保留 CLI 后端，按需要增加深集成后端。
4. 整理版本、安装包必需资源和可选依赖测试；将文章等垂直业务能力留在插件/配方层。

优先级判断：先证明一个任务能安全停下、可靠恢复、独立验收，再增加运行频率和自治时长。
