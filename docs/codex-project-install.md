# Codex 项目级安装与诊断

`prax install --target codex --local` 接入的是当前项目的 **Codex repo skill**，
不是用户级 marketplace 插件。不调用 `codex plugin add`，不修改用户插件、
marketplace、Codex 配置或插件缓存。

## 已核实的加载机制

Codex 官方支持可信项目的 `.codex/config.toml`，但声明 marketplace、
启用插件与实际安装/加载并不相同。现有用户插件安装器的 `--root` 只改变
复制目录，不能当作项目隔离。见 [官方配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)。

本机 **Codex CLI 0.153.4** 的隔离离线实测：

- 项目 marketplace + plugins.enabled：可以列出插件，但 `installed=false`，技能未加载。
- 任意目录 + skills.config.path：配置可读取，不会新增技能扫描根。
- `.codex/skills/<name>/SKILL.md`：skills/list 实际返回精确路径、`scope=repo`、`pluginId=null`。

官方推荐的仓库目录目前是 `.agents/skills`；本次使用已实测仍支持的
`.codex/skills` 兼容发现机制，避开业务仓自己的镜像重建。安装器逐次
探测，不把此版本的行为当成所有未来版本的保证。见
[官方技能说明](https://learn.chatgpt.com/docs/build-skills)。

## 安装与文件保护

在业务仓根目录使用所需版本的 Prax，例如已有项目虚拟环境时：

```bash
.prax/venv/bin/prax install --target codex --local --dry-run --json
.prax/venv/bin/prax install --target codex --local --json
.prax/venv/bin/prax doctor --target codex --json
```

只产生两项受管理文件：

- `.codex/skills/prax-shift/SKILL.md`：随包技能加项目虚拟环境入口说明。
- `.prax/codex-install.json`：项目身份、内容版本、来源及文件 SHA256。

不改 harness、adapter、`.codex/config.toml`、业务代码、`.claude/skills`
或 `.agents/skills`。`--local` 不与 `--root` 组合，也不能用于 Claude。
不启动 worker、验收、调度、Git 写入或部署。

同内容重复安装不改文件/收据字节；新版只更新与上次受管理指纹仍一致的
文件。外来同名文件、用户修改、损坏收据、软链接、普通文件占用父目录时，
在写入前拒绝，不覆盖、不删除、不自动认领。

## doctor 的含义

`doctor` 是只读诊断入口，不是安装器或激活器。修改它是为了让诊断对象与
项目安装范围一致，避免用旧全局插件的健康状态证明本地接入成功；它不会
让当前 Codex 任务切换实例，也不会自动重载任务中的技能。

Git 仓库内的 `doctor --target codex` 默认检查最近项目，即使本地缺失也
不回落到旧全局插件。非 Git 目录可用 `--local` 明确选择当前目录。
`--global` 显式检查用户插件；`--root` 仍是旧用户插件的自定义资源目录。

项目输出包含 scope、mechanism、实际技能/收据路径、安装及期望版本、
当前 Prax 解释器/包路径、Codex 版本、各项检查和 missing。除文件指纹
一致外，还要求原生发现返回精确路径、repo 范围、启用、无插件 ID、无加载错误。
项目 skills.config 的显式 false 单独阻断；本机 skills/list 可能漏报该项目层设置。
原生探测前后还会比较配置、收据与技能的字节快照；中途出现修改或删除时
返回 degraded，要求重新诊断，保留其他窗口的改动。通过仅反映受检时刻的状态。

探针仅使用 [app-server skills/list](https://learn.chatgpt.com/docs/app-server)；
没有 thread/start、turn/start、模型请求或插件激活。macOS sandbox-exec
禁止网络和临时目录外写入，HOME/CODEX_HOME/XDG 临时隔离。项目 trust
仅写在该临时环境，不更改真实用户信任设置。缺少沙箱或 CLI 时拒绝裸跑、
拒绝全局回退；当前只提供这个 macOS 探针。

`status=ok` 表示项目文件和隔离原生发现通过，**不表示当前任务已重新加载**；
`activation_verified` 始终为 false。真实用户设置、管理员策略、当前任务
缓存仍可能影响最终可见性。新开该项目的 Codex 任务，选择项目范围的
prax-shift，不要混用保留的旧全局插件。无需为了安装而调用技能。

安装后发现失败返回 degraded/非零退出并保留待诊断文件，不把“复制完”
冒充接入成功。既有用户插件 repair/uninstall 不是项目 skill 的维护入口。

## 离线验证

```bash
python -m pytest tests/unit/test_codex_project_install.py \
  tests/unit/test_codex_project_cli.py tests/unit/test_codex_project_probe.py \
  tests/unit/test_codex_integration.py tests/test_openprax_claude_code.py -q
```

覆盖作用域路由、旧全局假阳性、幂等、原文件/禁用设置保护、软链接与
父目录冲突、错误发现元数据、无 CLI/沙箱失败关闭，以及真实 macOS 沙箱
拒绝项目写入和网络连接。不运行真实 coding worker。

### 2026-09-10 本机接入记录

定向离线回归 **93 项通过**（82 项新增，11 项既有）；不是全仓测试通过声明。
在 `aipb-chat` 已有 `.prax/venv` 中仅替换 Prax 包，依赖总数仍为 19。

- 安装前，默认 doctor 返回项目范围、degraded、退出码 2；旧全局插件未掩盖本地缺失。
- 安装后，原生发现返回项目内精确技能路径、repo 范围、enabled=true、pluginId=null。
- 安装内容版本为 `1.0.0+codex.project-9100306e174bdc0a`，doctor 缺失项为空。
- 第二次安装 `changed=false`、`changed_files=[]`；技能格式校验通过。
- 补充探测期间收据被修改/删除的升级回归；任何写入前整体复核，保留原技能及他人改动。
- doctor 补充探测期间技能、收据或禁用配置变化的回归；拒绝旧状态假阳性，
  并验证成功诊断也不会继承探针对当前任务激活状态的过度声明。
- 既有 harness、adapter、项目 Codex 配置指纹及既有受保护目录差异摘要保持一致；
  `.agents/skills` 未写入，全局 marketplace 与旧插件 manifest 指纹保持一致。
- 用户 Codex 配置与最初基线不同，文件修改时间早于本次实际安装；本次诊断前后
  指纹一致。未覆盖或还原该文件，也不把这个差异归因于未经核实的其他进程。
- 现有 harness 示例仅执行 plan，返回 `execution=not-started`，保留 worker=codex
  和原 adapter；没有运行 worker、验收、业务测试、定时任务或 Git 写操作。

这些证据证明安装及只读发现，不证明当前 Codex 任务已激活技能，也不证明业务验收通过。
