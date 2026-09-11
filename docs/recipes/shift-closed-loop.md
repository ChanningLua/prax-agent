# Prax Shift：从交接到验收的最小闭环

目标：完成一个已授权、低风险、可独立验收的代码任务。
不是先开一个无限循环，再期待它自己定义工作与成功。

在支持的宿主里调用 `prax-shift`，用自然语言交接目标、仓库和验收。
随包技能会协助整理下列文件。以下模板需要换成实际分支、路径和测试；
不要覆盖已有任务。仅支持 macOS/Linux 的当前 code-check 运行时。

## 1. 定义工作环境与任务

`.prax/harness.json`：

```json
{
  "schemaVersion": 1,
  "worker": "codex",
  "policy_files": []
}
```

Claude 使用 `"worker": "claude"`。Codex 默认 workspace-write，Claude
默认 dontAsk；后者会拒绝仍需审批的操作，不是文件系统沙箱。
修改宿主、模型或权限应由人决定，不做自动回退。可选 `model` 固定模型；
Claude 必须提供明确的 `claude-` 模型 ID。

`.prax/task.json`：

```json
{
  "schemaVersion": 1,
  "id": "parser-empty-input",
  "goal": "Make the parser return an empty result for empty input.",
  "kind": "code-check",
  "repositories": [
    {"path": ".", "branch": "main", "scope": ["src/parser.py"]}
  ],
  "verifier": {
    "argv": ["python3", "-m", "pytest", "tests/test_parser.py", "-q"],
    "cwd": ".",
    "timeout_seconds": 60
  },
  "budget": {"max_iterations": 3, "wall_seconds": 600, "stuck_after": 2}
}
```

测试必须已能验证目标，验收文件不能由编码 worker 随手改写。额外的
验收数据/脚本用 `verifier.control_files` 显式绑定，例如
`["tests/fixtures/parser-cases.json"]`。所有此类路径相对工作区根目录。
自动文件指纹并不覆盖所有外部依赖或动态导入；必要时补充控制文件。

预算是同一 task ID 的累计额度，不是每次定时触发就重置。
将 `.prax/harness-runs/` 加入项目 ignore；不要提交私有运行日志。

## 2. 先校验，再接班

```bash
prax shift plan --task .prax/task.json
prax shift run --task .prax/task.json
```

Plan 不会调用 worker 或验收命令。Run 会先验收：已经通过就零派工；
环境正常但测试失败才进入修复。没有安装 `prax` 命令时，可在已安装
依赖的环境中使用 `python -m prax`，不要隐式使用另一个项目的代码。
已识别测试运行器的全跳过、零测试或仅收集测试，不算验收通过。
自定义脚本/报告格式仍需由作者保证验证有效；退出码不能证明测试覆盖充分。

## 3. 查看、暂停和恢复

```bash
prax shift status --run-id parser-empty-input
prax shift pause --run-id parser-empty-input
prax shift resume --run-id parser-empty-input
```

Pause 命令返回“已请求暂停”，不代表清理已经结束；用 status 确认。
暂停不回滚代码。恢复前核对任务、验收、分支、HEAD、index 和最后的
工作区检查点。人或其他窗口的后续修改可能触发漂移拒绝，这是保护机制；
不要删除 evidence、改 budget 或自动换 ID 来绕过它。
宿主报错前的合法部分修改，只有通过范围、控制文件及释放检查，才会保存
为可恢复检查点；恢复时先重新验收，不补预算。验收命令及生命周期检查/
释放钩子不能修改被验收源码，否则本轮不保留 PASS。

| 结果 | run/resume 退出码 | 处理 |
|---|---:|---|
| PASS 且 verified=true | 0 | 声明的验收通过；不代表已提交/发布 |
| FEATURE-FAIL | 1 | 预算内仍未通过；检查失败证据 |
| HARNESS-ERROR | 2 | 环境/执行/身份/所有权问题，需要处理阻塞 |
| PAUSED | 3 | 等待人明确恢复 |

Status 只读保存结果，读取成功会 exit 0，不能当作再次验收。
证据位于 `.prax/harness-runs/parser-empty-input/`：契约、检查点、运行日志、
验收日志、worker journal 和 result.json。强杀监督器后若留下 RUNNING，
系统拒绝自动接班；先检查进程及所有权，不隐式补预算。
运行时通过父生命周期管道感知监督器死亡并回收进程组，同时继承仓库锁，
避免监督器先退出、旧运行时未停止时新任务抢占仓库。

## 4. 需要时再安排定时接班

只有明确需要 Prax 系统调度时才执行：

```bash
prax cron add --name parser-repair --schedule "0 * * * *" \
  --run-mode shift --task .prax/task.json
prax cron install
```

在 Codex 应用内优先用宿主提供的调度机制，在固定工作目录重放：

```bash
prax shift run --task .prax/task.json --resume-existing
```

两者都不能自动解除暂停或续费预算；不要同时配置两个调度器。
完成后会复用通过身份检查的结果，不重复派工。Prax cron 配置了通知时，
相同任务与停止原因的重复结果不重复通知。宿主调度需单独保留安静通知策略。
通知渠道缺失或发送失败时不记录“已送达”，下一次调度机会会重试。
`.prax/logs/cron/` 属于运行日志，不计入源码漂移；建议加入 ignore。

这是一条有界任务闭环，不等于“每天自动发现新工作”。每日巡检、任务
准入和新预算审批仍需另行设计。旧 goal/feature 模式继续兼容，但旧的
`--run-mode shift --prompt ...` 定时配置可查看/删除，但会标为
`migration-required` 并禁止派工，不阻塞同一配置中的有效任务。
先人工整理 task 文件，再删除旧配置并用 `--task` 添加，不自动编造验收和预算。
