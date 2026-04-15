<div align="center">

# Prax

**驱动 LLM Agent 在真实代码库上执行 测试-验证-修复 循环的 CLI 工具**

<br>

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

[快速开始](#快速开始) · [为什么选 Prax](#为什么选-prax) · [使用示例](#使用示例) · [基准测试](#基准测试) · [配置](#配置) · [架构](#架构) · [参与贡献](#参与贡献)

<br>

</div>

---

## 快速开始

```bash
git clone https://github.com/ChanningLua/prax-agent.git
cd prax
pip install -e .

export ANTHROPIC_API_KEY=your_key_here

# 使用原生运行时执行任务
prax --runtime-path native "run pytest -q, fix the failure, and stop when tests pass"
```

Prax 会检查你的代码库、运行测试、编辑文件，并在循环中验证结果。它在会话之间保留上下文，后续任务可以从上次中断的地方继续。

> Prax 可以代你执行 shell 命令。默认使用 `workspace-write` 模式——项目外的文件不可触碰。使用 `--permission-mode read-only` 可安全浏览。

---

## 为什么选 Prax

**Prax 不只是又一个 LLM 封装——它是为真实仓库工作打造的生产级 Agent 运行时。**

<p align="center">
  <img src="./docs/assets/capabilities.zh-CN.svg" alt="Agent Capabilities" width="800">
</p>

### 验证优先架构

<p align="center">
  <img src="./docs/assets/verification-loop.svg" alt="Verification Loop" width="800">
</p>

大多数工具发送 prompt 然后听天由命。Prax 运行 **测试-验证-修复循环**：执行测试套件、分析失败、编辑代码、重新运行直到测试通过。验证层是一等公民——不是事后补丁。

**基准验证**: 10/10 仓库修复任务全部解决，平均 29.56 秒（对比同类框架基线 8/10）。

**跨会话持久记忆** — 关闭终端不会丢失上下文。三种记忆后端：JSON（零配置）、SQLite（全文搜索）、OpenViking（向量嵌入）。

**多模型编排** — Claude、GPT、GLM 及自定义模型，具备显式路由、降级链和成本追踪。会话中随时切换模型 `/model claude-opus-4-6`。

**安全内建** — 权限模式（`read-only`、`workspace-write`、`danger-full-access`）、Schema 校验、工作区边界、完整审计追踪。

**为真实代码库而生** — 25+ 内置工具、中间件管线（循环检测、质量门禁）、多语言支持、交互式 REPL 模式。

**透明可度量** — 实时成本追踪、会话历史与回放、内置基准测试套件、开放架构可扩展自定义组件。

---

## 使用示例

### 仓库修复

```
$ prax "run pytest -q, fix the failure, and stop when tests pass"
▶ VerifyCommand {"command": "pytest -q"}
  ✗ FAILED test_auth.py::test_login - AssertionError
▶ Read {"file_path": "src/auth.py"}
▶ Edit {"file_path": "src/auth.py", ...}
▶ VerifyCommand {"command": "pytest -q"}
  ✓ 1 passed in 0.12s
Verification passed. Task complete.
```

### 一次性任务

```bash
prax "explain the authentication flow in login.py"
prax "refactor auth.py error handling, replace requests with httpx"
prax "analyze project architecture, list technical debt, prioritize by impact"
```

### 交互式 REPL

```bash
prax repl

> analyze the codebase structure
> fix the SQL injection in user_query.py
> /model claude-opus-4-6
> /cost
Session: 12.4K tokens ($0.04)
```

### 斜杠命令

```
/model, /session list, /plan, /todo show, /doctor, /cost, /help
```

---

## 基准测试

![Prax benchmark summary](./docs/assets/benchmark-frameworks.svg)

在 Claude 系列仓库修复测试套件上，与 Hermes、HyperAgents 和 oh-my-openagent 的内部基准对比：

| 运行 | 解决数 | 平均耗时 |
|---|---:|---:|
| 跨框架基线 | `8/10` | `58.44s` |
| 最新 Prax 单独重跑 | `10/10` | `29.56s` |

每个框架在真实仓库修复任务上运行 10 轮，保留会话状态。

![Prax benchmark improvement](./docs/assets/benchmark-prax-improvement.svg)

提升来自 Agent 循环中的验证和收敛修复，而非更改基准任务本身。这仅衡量仓库修复能力——不包括开放域研究或长时间规划。

详见 [docs/BENCHMARKS.md](./docs/BENCHMARKS.md) 了解方法论和原始数据。

---

## 配置

**模型** — 在项目中创建 `.prax/models.yaml`：

```yaml
default_model: claude-sonnet-4-6

providers:
  anthropic:
    base_url: https://api.anthropic.com
    api_key_env: ANTHROPIC_API_KEY
    format: anthropic
    models:
      - name: claude-sonnet-4-6

  openai:
    base_url: https://api.openai.com/v1
    api_key_env: OPENAI_API_KEY
    format: openai
    models:
      - name: gpt-4.1
```

或者：`prax /init-models claude`

**权限模式**

| 模式 | 允许的操作 | 默认 |
|------|-----------|------|
| `read-only` | 不可写文件，不可执行 shell 命令 | |
| `workspace-write` | 可修改项目内的文件 | ✓ |
| `danger-full-access` | 无限制 | |

```bash
prax --permission-mode read-only "analyze security vulnerabilities"
```

**运行时路径**

| 参数 | 行为 |
|------|------|
| `--runtime-path auto` | 如果安装了 `claude` 则使用 Claude CLI 桥接，否则使用原生运行时（默认） |
| `--runtime-path native` | 始终使用原生运行时 |
| `--runtime-path bridge` | 始终使用 Claude CLI 桥接；未安装 `claude` 时报错 |

**数据目录**

| 路径 | 内容 |
|------|------|
| `.prax/sessions/` | 对话历史 |
| `.prax/memory.json` | 项目记忆（自动提取的事实） |
| `.prax/todos.json` | 当前任务列表 |
| `.prax/agents/` | 自定义 Agent 定义 |
| `.prax/models.yaml` | 模型配置 |
| `~/.prax/` | 全局配置（跨项目） |

---

## 架构

<p align="center">
  <img src="./docs/assets/architecture.svg" alt="Architecture" width="800">
</p>

核心模块：

| 路径 | 职责 |
|------|------|
| `core/agent_loop.py` | 核心编排循环（最多 25 次迭代，熔断器） |
| `core/middleware.py` | VerificationGuidance、LoopDetection、QualityGate 等 |
| `tools/verify_command.py` | 有界验证（pytest、npm test、cargo test、go test） |
| `tools/sandbox_bash.py` | 自动降级：验证命令绕过沙箱开销 |
| `core/memory/` | 可插拔后端（local / SQLite / vector） |
| `core/llm_client.py` | Provider 注册表，多模型路由 |
| `agents/` | Ralph（规划者）、Sisyphus（执行者）、Team（并行） |
| `workflows/` | 任务分解与编排 |

---

## 参与贡献

欢迎贡献！详见 [CONTRIBUTING.md](CONTRIBUTING.md)：
- 开发环境搭建
- 代码风格规范
- 测试要求
- PR 流程

基准测试和可复现性相关工作，另见 [docs/BENCHMARKS.md](./docs/BENCHMARKS.md)。

---

## 许可证

MIT License — 详见 [LICENSE](LICENSE)。
