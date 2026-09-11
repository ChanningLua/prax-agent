# Prax

**把工作交给 Agent，把关键决策留给人。**

Prax 是一个开源的 Agent 工作框架，让任务有明确范围、执行有章可循、结果有据可查。

[English](README.md) · [简体中文](README.zh-CN.md) · [快速开始](#快速开始) · [核心理念](docs/CORE-PRINCIPLES.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](pyproject.toml)

## Prax 是什么？

你可以直接用 Prax 分析代码、修改文件、整理资料，也可以让它配合 Claude Code 或 Codex，完成需要多步执行和反复验收的任务。

它关注的不只是“Agent 能不能做”，还有：在哪个项目做、允许改什么、怎样判断完成，以及遇到问题时何时停下。Prax 通过任务配置、执行控制和结果记录，让这些工作便于检查和交接。

**Prax Shift 是其中的持续工作模式，不是 Prax 的全部。** 除了编码托管，Prax 还提供技能扩展、工具接入、会话与记忆组件，以及知识整理和内容写作流程。

## 可以用它做什么

| 你的需求 | 使用方式 | Prax 如何帮助你 |
|---|---|---|
| 分析和修改代码 | Prax 自带的命令行 Agent 与交互会话 | 读取代码、编辑文件、执行命令，并保存会话记录 |
| 托管一项编码任务 | Prax Shift，使用 Claude 或 Codex 执行编码 | 固定任务范围和验收条件，在约定的次数与时间内修复并检查结果 |
| 只跑一次测试，不改代码 | 一次性验证模式 `verification` | 复用项目已有测试入口，分别记录各用例结果，不启动编码 Agent |
| 复用团队的工作方法 | 技能（Skills）、自定义 Agent 与 MCP 工具 | 保存常用流程，连接项目需要的工具和服务 |
| 整理资料、撰写内容 | 研究、知识整理与内容写作流程 | 生成草稿、来源记录、审查清单和质量报告 |
| 稍后继续处理任务 | 运行记录、定时调度和通知 | 在条件允许时继续已配置的任务，保留原有范围与预算 |

这些使用方式的约束不同。普通对话和技能主要提供执行能力与流程指导；需要固定任务范围、限制执行预算并独立验收时，应使用 Repository Harness（仓库任务运行框架）或基于它的 Shift 模式。

## 核心理念

> **Prax = Environment Runtime + Adaptive Autonomy Controller + Evidence Plane**

| 架构层 | 回答的问题 | 当前仓库任务中的作用 |
|---|---|---|
| **Environment Runtime · 环境运行时** | Agent 在哪个环境中工作？ | 核对仓库和分支，保存任务配置与运行状态，协调仓库占用并管理执行进程 |
| **Adaptive Autonomy Controller · 自适应自治控制器** | 什么时候继续执行，什么时候需要人来处理？ | 限制修改范围、运行时间和尝试次数；遇到异常时停止，支持人工暂停和恢复 |
| **Evidence Plane · 证据层** | 如何判断任务完成，依据在哪里？ | 收集测试结果、日志和报告，校验产物文件，保留可追溯的执行记录 |

**人来定目标和权限，Agent 按约定执行，最后用结果验收。**

HITL（Human in the Loop）指人在关键环节参与决策；AFK（Away From Keyboard）指人不必全程守在键盘前。两者可以结合：人先明确目标、权限和验收条件，Agent 在约定范围内执行；遇到无法继续的问题，再交回人来判断。

这个公式描述的是 Prax 的架构思路，并非三个独立产品。目前的执行控制基于明确规则，尚未实现自主学习的风险判断或统一审批平台。详见[核心理念](docs/CORE-PRINCIPLES.md)。

## 快速开始

本页以 `main` 分支的源码为准。已发布的软件包可能尚未包含这里介绍的新功能，建议先从源码安装。

### 安装 Prax

需要 macOS 或 Linux，以及 Python 3.10+。按下面的方式安装 Prax 不需要 Node.js；Claude、Codex 和项目测试工具的依赖需另外准备。

```bash
git clone https://github.com/ChanningLua/prax-agent.git
cd prax-agent
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/prax --version
.venv/bin/prax shift --help
```

最后两条命令只查看版本和帮助，不调用模型、执行测试或启动定时任务。能正常显示这些信息，说明命令可用，但还需要检查项目的实际执行条件。

### 选择使用方式

- **直接使用 Prax Agent**：配置模型接口地址和凭证，再使用 `prax prompt` 或 `prax repl`。见[使用入门](docs/getting-started.md#native-runtime)。
- **在 Codex 中使用**：先把 Prax 安装到目标仓库的 `.prax/venv`，再安装项目技能并检查是否可用。见[项目接入](docs/getting-started.md#codex-project-setup)。
- **让 Claude 执行编码任务**：在仓库配置中指定 `worker: "claude"`。通过命令行执行这类任务，无需先向 Claude 安装 Prax 技能。见[仓库任务配置](docs/repository-harness.md)。
- **接入已有 E2E**：按[一次性验证协议](docs/verification-tasks.md)编写项目适配代码，调用现有测试系统并检查其报告。

不要用示例覆盖项目已有的环境、配置、任务或适配代码。建议从一个小任务开始，先确认它的验收标准明确，相关测试也已能在本地运行。

### Codex 项目级接入

确认 Prax 已安装到**目标仓库**的 `.prax/venv`，在该仓库执行：

```bash
.prax/venv/bin/prax install --target codex --local --dry-run --json
.prax/venv/bin/prax install --target codex --local --json
.prax/venv/bin/prax doctor --target codex --json
```

安装器负责管理 `.codex/skills/prax-shift/SKILL.md` 和 `.prax/codex-install.json`，不会写入 `.agents/skills`、用户级插件配置或插件市场配置。除了检查文件，它还会调用 Codex 的技能发现接口，确认该项目技能能被识别。

这项发现检查在隔离环境中进行，**目前仅支持 macOS**，且需要已安装的 Codex CLI。条件不满足时会报告失败，不会改用全局插件。即使 doctor 检查通过，已经打开的 Codex 任务也不一定加载了新技能；请新开项目任务，选择属于该项目的 `prax-shift`。

[Codex 官方技能目录](https://learn.chatgpt.com/docs/build-skills)是 `.agents/skills`。Prax 目前使用兼容目录 `.codex/skills`，并在每次安装和诊断时检查当前 Codex 是否支持它。详见[项目级安装与诊断](docs/codex-project-install.md)。

### Claude Code 集成

Prax 也为 Claude 提供技能、命令、Agent、规则、钩子（hooks）和 MCP 配置。`prax install --target claude` 目前是**用户级安装**，可能改动 `~/.claude` 并影响其他项目，与 Codex 的 `--local` 不同。使用前请阅读[安装范围与注意事项](docs/getting-started.md#claude-integration)。

## 把编码任务交给 Prax Shift

使用已安装的 Prax 技能时，可以直接这样描述任务：

> 用 Prax Shift 修复解析器处理空输入时报错的问题。只改解析器，不改测试，用现有回归测试验收。最多让编码 Agent 尝试三次，总共不超过十分钟。不要提交或发布，先给我看计划。

技能会帮助你把要求整理成两份配置，供你检查：

- `.prax/harness.json`：由哪个编码 Agent 执行（`worker`）、需要遵守的项目规则，以及可选的适配器。
- `.prax/task.json`：任务 ID、目标、实际仓库和分支、允许修改的文件、验收方法，以及总次数和时间限制。

按实际项目填好配置后，使用项目环境中的 Prax 检查计划。如果只在源码目录安装了 Prax，请将下面的命令路径换成对应的绝对路径，避免误用旧的全局版本：

```bash
.prax/venv/bin/prax shift plan --task .prax/task.json
```

`plan` 检查任务配置和当前仓库状态，**不跑测试，也不启动编码 Agent**。确认任务后，可以按需运行、查看、暂停或恢复任务；以下命令不需要全部依次执行：

```bash
.prax/venv/bin/prax shift run --task .prax/task.json
.prax/venv/bin/prax shift status --run-id <task-id>
.prax/venv/bin/prax shift pause --run-id <task-id>
.prax/venv/bin/prax shift resume --run-id <task-id>
```

`code-check` 会先运行验收测试。如果已经通过，就无需调用编码 Agent；如果是功能问题，则在约定的次数和时间内尝试修复；如果是环境故障或仓库状态异常，则停止执行。

恢复任务前，Prax 会核对任务配置和此前保存的代码状态。已经用掉的次数和时间会继续计入总预算，不会重新清零。暂停只会停止执行，不会撤销已有修改。

结果保存在 `.prax/harness-runs/<task-id>/`。`PASS` 且 `verified=true` 表示任务中指定的验收已通过，不代表已经部署、覆盖全部业务或通过所有平台测试。`status` 只读取已有结果，不会重新运行验收。

如果需要定时继续任务，必须另行配置。Shift 的定时调度只处理同一个任务，不自动生成新任务、增加预算或解除人工暂停。见[持续工作指南](docs/recipes/shift-closed-loop.md)。

## 只测试，不改代码

“完整跑一次测试”和“修到测试通过”是两种任务。前者使用 `kind: "verification"`。

**Prax 管运行和证据，现有测试系统管实际测试。**

项目适配代码负责调用现有测试入口，确认本次获准测试的平台和用例，并检查测试系统生成的报告。Prax 则核对报告是否属于本次运行、退出码是否与结果一致，以及产物文件的路径和哈希是否有效。

- 分别报告通过（`passed`）、失败（`failed`）、阻塞（`blocked`）和未执行（`not-executed`）。
- 保留具体阻塞原因，不把“没运行”算作“业务测试失败”。
- 覆盖情况仍标记为 `unknown`：统计跑了多少用例，不等于知道覆盖了多少业务功能。
- 只运行一次，不启动编码 Agent，也不自动重跑、恢复或安排周期验证。

仅凭进程正常退出，或输出了一段 JSON，不能判定测试通过。不同项目需要提供自己的适配代码，安装 Prax 不会自动接通所有 E2E 系统。见[一次性验证协议](docs/verification-tasks.md)。

## 不止编码：扩展你的工作流

除了仓库任务管理，Prax 还提供以下能力：

- **技能与 Agent**：加载内置或项目自定义技能，按需读取操作指引，复用常用工作方法。技能用于指导行为，不能代替安全隔离。
- **工具与 MCP**：连接本地工具和已配置的服务。每项接入都需要单独管理凭证和操作权限。
- **模型与会话**：配置模型服务、选择模型并保存对话记录。Prax 自己使用的模型 API，与 Claude/Codex 编码 Agent 的登录认证相互独立。
- **记忆组件**：保存纠错记录和经验，提供本地文件、SQLite 等存储方式。不同运行方式接入的记忆能力并不相同，不能认为每次对话都会使用所有后端；记录下来的推断也仍需核实。
- **内容流程**：支持[批量撰写文章](docs/recipes/article-batch.md)、[整理来源并生成草稿](docs/recipes/hotspot-article.md)、[编写发布说明](docs/recipes/release-notes.md)、[检查文档是否跟上代码](docs/recipes/docs-audit.md)和[汇总工单](docs/recipes/support-digest.md)。外部工具需单独配置；内容质量检查不能保证每条事实都正确，也不包含自动发布授权。

## 使用前需要了解的限制

- **Prax 不是操作系统沙箱。** 它在执行前后及关键步骤检查仓库变化，但不能阻止所有副作用，也不会自动回滚。测试和适配器都能执行代码，使用前必须确认它们可信。
- **Claude 与 Codex 的权限机制不同。** Codex 编码任务默认使用 `workspace-write`，Claude 使用 `dontAsk`，二者不具备相同的隔离能力。
- **验收是否可靠，取决于测试是否可靠。** 已识别出的零用例或全部跳过不能算通过；自定义验收脚本仍需自己保证判断正确。
- **保存了状态，也可能无法直接恢复。** 进程被强制结束、子进程脱离管理、代码被外部修改或仓库被其他任务占用时，可能需要人工检查，再创建新任务。
- **持续工作仍有明确限制。** 当前尚未提供自动生成每日任务、统一双向审批、自动发布到生产环境，或一键停止全部任务的全局开关。
- **历史测试成绩不能代表所有场景。** [基准记录](docs/BENCHMARKS.md)只针对一组历史实验，不是通用排行榜，也不能代替当前版本的端到端验收。

旧命令 `shift "goal" --verify ...` 和 `--features` 仍可兼容使用，但不具备 `shift run --task` 的全部保护措施。

## 文档导航

| 文档 | 内容 |
|---|---|
| [开始使用](docs/getting-started.md) | 安装 Prax、配置模型、接入 Claude 或 Codex |
| [核心理念](docs/CORE-PRINCIPLES.md) | 产品方向、人与 Agent 的分工，以及当前实现情况 |
| [Repository Harness](docs/repository-harness.md) | 任务配置、项目适配、状态恢复与验收结果 |
| [Prax Shift](docs/prax-shift-spec.md) | 持续执行、任务交接与定时调度 |
| [一次性验证](docs/verification-tasks.md) | 如何接入已有测试系统并检查报告 |
| [Codex 项目接入](docs/codex-project-install.md) | 项目级安装、技能识别、文件保护与问题诊断 |

## 参与贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。欢迎提交改进建议、可复现的问题和针对性测试，并说明已验证的结果与尚未覆盖的情况。目前重点关注 Claude/Codex 的实际运行验证、跨平台隔离、记忆功能接入，以及可重复执行的评估测试。

[MIT License](LICENSE)。
