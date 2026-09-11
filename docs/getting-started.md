# 开始使用 Prax

先确定你想怎样使用 Prax，再准备相应的环境。让 Claude 或 Codex 执行编码任务，与让 Prax 直接调用模型 API，是两种不同的用法；安装好技能，也不代表 Prax 已经能运行项目里的业务测试。

[返回中文介绍](../README.zh-CN.md) · [英文介绍](../README.md)

本页以 `main` 分支的源码为准。请按需要选择下面的安装和使用步骤；实际运行任务、修改业务代码或开启定时任务，都需要另行确认，不能从安装操作中推定已获授权。

## 1. 从源码安装

需要 Python 3.10 或更高版本。Prax 的仓库任务运行框架（Repository Harness）支持 macOS 和 Linux。这种安装方式不需要 Node.js，但 Claude、Codex 或项目测试工具可能有各自的依赖。

在准备保存源码的目录中执行以下命令。如果已经下载过源码或创建过虚拟环境，先检查现有版本，不要覆盖重建：

```bash
git clone https://github.com/ChanningLua/prax-agent.git
cd prax-agent
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/prax --version
.venv/bin/prax shift --help
```

最后两条命令只显示版本和帮助，不调用模型，也不启动编码执行器、测试或定时任务。版本号来自当前源码；它不能单独证明项目已经接入成功。

源码目录里的 `.venv` 用于直接使用或开发 Prax；业务仓库里的 `.prax/venv` 则用于该项目的集成。它们是两个独立环境。下文的 `/absolute/path/to/prax-agent` 和 `/path/to/your-project` 都需要替换成你电脑上的实际路径。

已发布的软件包可能落后于 `main`。Python 包和 npm 启动程序分别维护版本，后者也不会替你安装全部 Python 依赖。因此，体验本页介绍的最新功能时，建议从源码安装，而不是默认使用 `npm install -g`。

## 2. 选择你的使用方式

| 目标 | 需要准备 | 下一步 |
|---|---|---|
| 直接使用 Prax 的 Agent | 模型服务地址、凭证和配置 | [使用 Prax Agent](#native-runtime) |
| 在 Codex 项目任务中调用 Prax | 项目 `.prax/venv`、Codex 命令行工具，以及 macOS 的隔离检查支持 | [接入 Codex 项目](#codex-project-setup) |
| 让 Claude 或 Codex 执行编码任务 | 已登录且可用的相应命令行工具、明确的任务和验收方式 | [交接仓库任务](#repository-task) |
| 把工作流安装进 Claude | 确认接受可能影响其他项目的用户级安装 | [安装 Claude 集成](#claude-integration) |
| 只运行一次已有测试 | 项目测试命令、本次授权，以及传递和校验测试证据的适配代码 | [一次性验证](verification-tasks.md) |

<a id="native-runtime"></a>

## 直接使用 Prax Agent

这种用法由 Prax 直接调用你配置的模型 API，不借用 Claude 或 Codex 的交互会话。模型服务可能收费，仓库内容也可能随请求发送给服务提供方。使用前请确认费用、数据传输和凭证管理要求。

在目标项目的 `.prax/models.yaml` 中填写模型服务配置。如果文件已经存在，保留原内容，只合并需要的配置：

```yaml
providers:
  default:
    base_url_env: PRAX_LLM_BASE_URL
    api_key_env: PRAX_LLM_API_KEY
    format: openai
    models:
      - name: your-model-id
default_model: your-model-id
```

将 `your-model-id` 换成服务实际支持的模型标识，并根据接口协议将 `format` 设为 `openai` 或 `anthropic`。环境变量名需要与上面的 YAML 配置一致。请通过安全的凭证管理方式向当前进程提供服务地址和密钥；不要把真实密钥写进文档、命令历史或 Git，也不要用 `echo` 打印密钥来排查问题。

进入目标项目目录后，用源码虚拟环境中 Prax 程序的完整路径检查配置：

```bash
/absolute/path/to/prax-agent/.venv/bin/prax providers
```

`available` 只表示程序能读取所需凭证，不代表服务器能够连接、账户额度充足或模型调用一定成功。确认可以调用所选模型后，先试一个只分析、不修改文件的请求：

```bash
/absolute/path/to/prax-agent/.venv/bin/prax prompt \
  "阅读 README.md，提出三条改进建议，不要修改项目文件。" \
  --permission-mode read-only
```

这里的只读模式限制的是 Agent 使用工具的权限，并不代表整个进程完全不写磁盘：Prax 仍可能保存会话和运行状态。普通 `prompt` 请求也不会自动获得仓库任务框架的任务冻结和保护机制。

要连续对话，可以使用 `prax repl`。下文有些示例会简写为 `prax`；使用前请确认这个命令来自你选定的虚拟环境，避免误用旧的全局版本。

<a id="codex-project-setup"></a>

## 接入 Codex 项目

Prax 会在隔离环境中检查 Codex 是否确实识别了项目技能。目前这项检查依赖 **macOS、sandbox-exec 和可用的 Codex 命令行工具**。Linux 暂时无法完成同样的安装验证；条件不满足时应停止，不要绕过检查或改装成用户级插件。

项目技能固定使用 `.prax/venv/bin/prax`。进入目标仓库，仅在这个虚拟环境尚不存在时创建它：

```bash
cd /path/to/your-project
python3 -m venv .prax/venv
.prax/venv/bin/python -m pip install /absolute/path/to/prax-agent
.prax/venv/bin/prax --version
```

如果已有 `.prax/venv`，先检查其中 Prax 的版本和来源，再决定是否升级，不要重建。上面的安装会将 Prax 复制到项目环境中，而不是直接引用源码目录；这样，之后修改 Prax 源码不会自动改变业务仓库正在使用的版本。

仍在这个业务仓库中，先预览安装内容，再安装并检查结果：

```bash
.prax/venv/bin/prax install --target codex --local --dry-run --json
.prax/venv/bin/prax install --target codex --local --json
.prax/venv/bin/prax doctor --target codex --json
```

安装器只管理项目技能及其安装记录，不修改已有的任务配置、仓库适配器、用户插件市场配置或全局插件，也不会无条件覆盖用户改过的同名文件。项目 `doctor` 是只读检查，会列出安装范围、实际路径、版本和缺失项。在仓库内执行时，它默认检查当前项目，不会因为旧的全局插件正常就忽略本地安装缺失。

`status=ok` 表示检查时项目文件正常，而且隔离环境中的 Codex 确实识别了技能，**不代表当前已打开的任务加载了它**。请新开一个目标项目的 Codex 任务，选择项目范围的 `prax-shift`，先让它给出计划。实际使用时的权限和登录状态仍需单独确认。

Prax 当前使用 `.codex/skills` 兼容目录，并在实际安装和诊断时检查当前 Codex 版本能否识别；[官方推荐目录](https://learn.chatgpt.com/docs/build-skills)是 `.agents/skills`，不要把两种安装方式混用。具体机制和限制见[项目安装文档](codex-project-install.md)。

现有 `repair/uninstall` 命令面向用户级插件，不用于维护这个项目技能。不要用它们清理项目安装，也不要删除整个 `.prax` 目录。

<a id="repository-task"></a>

## 交接仓库任务

通过命令行运行按契约定义的任务，不要求先向 Claude 或 Codex 安装 Prax 技能。你需要 Prax 本身、选定编码执行器的可用命令行工具，以及仓库中已经能独立验收目标的测试。

按照[仓库任务框架文档](repository-harness.md)准备 `.prax/harness.json` 和 `.prax/task.json`，也可以让已安装的技能帮助起草。文档模板不能原样运行：需要填入实际分支、现有文件、明确且尽量小的修改范围，以及整个任务的预算；验收条件和相关测试文件也必须预先确定，在执行期间保持不变。已有配置应保留，修改前先审查需要合并的内容。

准备好后，用你已经确认来源的 Prax 命令检查计划：

```bash
prax shift plan --task .prax/task.json
```

正常的计划结果包含 `execution: not-started`。这一步不会调用仓库适配器、运行验收或启动编码执行器，因此也不能证明这些环节已经可用。`plan` 已经输出 JSON，不需要另加 `--json`；`shift --task` 则是直接运行任务的简写，不能用来预览。

确认这项任务后，再使用 `run` 开始执行。验收、暂停、恢复和交接的完整流程见[闭环指南](recipes/shift-closed-loop.md)。完成安装或查看计划，都不代表已经授权运行真实 E2E、操作设备、提交代码、推送或部署。

<a id="claude-integration"></a>

## 安装 Claude 集成

让 Claude 执行编码任务，与向 Claude 安装 Prax 资源，是两件不同的事。如果只通过命令行运行契约任务，在仓库配置中选择 `worker: "claude"` 即可，不需要做这一节的安装。

如果你希望在 Claude 中使用 Prax 的命令、技能、规则、事件钩子（hooks）和 MCP 配置，可以选择用户级安装：

```bash
prax install --target claude
prax doctor --target claude
```

这套安装默认修改 `~/.claude` 下的资源、`settings.json`、`.mcp.json`，以及安装记录和备份，可能影响其他项目。**它不支持 Codex 那种只影响当前项目的 `--local` 安装。** `--root` 只能改变安装目录，不能据此认为它具备相同的项目隔离和验证能力。

Claude 的 `doctor` 会更新安装状态和历史记录，也可能初始化记忆存储，因此不是纯只读操作。安装时即使加上 `--dry-run`，也可能创建目标根目录。如果你严格要求只读，或只允许影响一个项目，就不应使用这套命令来做无副作用检查。

## 调度与数据保护

安装 Prax 或项目技能不会自动开启定时任务。确实需要周期执行时，再单独配置调度。macOS 上的 `cron install` 会安装 LaunchAgent；Linux 上只输出一行 crontab 配置，需要你手动安装。如果所在应用已经提供调度功能，不要再用系统调度重复运行同一项任务。

保留 `.prax/harness.json` 等项目配置。按照仓库规范，不要把虚拟环境、凭证、会话记录和运行证据提交到 Git，例如 `.prax/venv/`、`.prax/harness-runs/`、`.prax/logs/cron/`。运行日志可能包含源码和业务数据，分享前请检查内容。

清理记忆前，先确认使用的是哪种存储方式、相关文件有什么用途，并做好备份。不要删除整个 `.prax/` 或 `~/.prax/`，这些目录里还可能保存配置和无法重建的运行证据。

## 接下来

- 编码托管：[Prax Shift](prax-shift-spec.md)。
- 只测不修：[一次性验证](verification-tasks.md)。
- 内容工作：[文章流水线](recipes/article-batch.md)、[热点文章](recipes/hotspot-article.md)。
- 提供反馈：[GitHub Issues](https://github.com/ChanningLua/prax-agent/issues)。请说明 Prax 版本、使用方式、涉及的范围、具体错误，以及哪些步骤尚未执行。提交前去除敏感信息，不要上传凭证。
