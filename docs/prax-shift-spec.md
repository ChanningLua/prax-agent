# Prax Shift — 受控执行闭环

状态：任务契约路径已实现，离线回归验证；尚不代表真实账户与常驻服务验收完成。
历史路线保留在 [v2 设计记录](history/prax-shift-v2.md)。

## 核心定位

Prax = Environment Runtime + Adaptive Autonomy Controller + Evidence Plane。

Shift 是这三个能力的一种工作模式，不是 Prax 的全部。长期运行的意义
不是永远循环，而是每次接班都知道任务是什么、能做什么、凭什么算完成。

- Environment Runtime：确定仓库、分支、工作区、工具和可复现的验收环境。
- Adaptive Autonomy Controller：在既定范围与预算内执行；暂停、环境故障、
  身份漂移或权限不足时停下，由人决定下一步。当前是明确规则，不是学习型策略。
- Evidence Plane：持久化冻结契约、代码检查点、派工记录、验收日志与结果。

## 一个内核，两个 worker

`prax shift plan/run/resume/status/pause` 通过 `commands/shift.py` 进入
`repository_harness.py`，与 `prax harness` 共用契约校验、仓库锁和监督进程。
不在 Shift、cron、Claude 和 Codex 各写一套“完成”的定义。

`.prax/harness.json` 固定 worker（`codex` 或 `claude`），可固定 model。
`.prax/task.json` 固定 ID、目标、仓库/分支/路径范围、独立验收和总预算。
技能负责把人的自然语言交接整理成文件，不让用户反复拼接长命令。

Codex 默认 `workspace-write`；Claude 默认 `dontAsk`，未预授权操作被拒绝。
二者权限机制不同，不能声称 Claude 已获得等价文件系统隔离。
真实宿主权限和认证必须单独实测，不自动改为绕过审批。

## 状态闭环

1. Plan：只校验，不运行测试或 worker。
2. Run：冻结契约、取得任务与仓库锁、检查代码身份，先跑验收。
3. 验收已通过：零 worker 调用，保存 PASS。
4. FEATURE-FAIL：把真实失败反馈给 worker，完成后再独立验收；总轮次、
   墙钟预算、重复失败检测决定何时停止。
5. HARNESS-ERROR：环境、权限、协议、控制文件或代码边界出错，停止执行。
6. Pause：持久化请求，监督器终止进程组，保留经过边界检查的部分工作。
7. Resume：核对冻结契约和检查点，沿用累计预算；只有显式恢复才解除暂停。
8. Handoff：提供任务 ID、状态、验收证据、剩余阻塞与确切的恢复命令。

`PASS + verified=true` 才是成功。进程 exit 0、worker 的“已修好”和
旧 journal 的成功不能代替它。旧 PASS 只有在控制与源代码身份一致时
才可复用；这不是对外部服务实时健康的重新验证。

## 调度契约

Prax cron 的 Shift 模式要求 `--task`，调用：

```bash
prax shift run --task .prax/task.json --resume-existing
```

它续跑同一任务，不生成随机 ID，不补充预算，不解除暂停。结构化结果
必须匹配 task ID，且进程 exit 0、状态 PASS、verified 为真才计为成功。
重复完成/失败通知按任务与停止原因去重，发送失败可在下次调度重试。

没有用户明确要求，不安装或启用调度。在 Codex 内优先遵循宿主的调度
工具规范，不为同一任务同时安装应用调度和系统 cron。

## 兼容边界与后续工作

旧 `shift "goal" --verify ...` 和 `--features` 保留，风险类别、approval relay
及三态验收能力也保留。旧循环现在拒绝跨 goal 复用 journal，并重查缓存
成功，但不具备新路径全部的冻结代码身份、仓库锁和墙钟监督保证。
原有无 task 的 Shift cron 必须迁移；普通 prompt cron 不受影响。

当前没有自动生产发布、push/merge、无限预算、宿主原生双向审批集成、
自动生成新的每日任务、全天持续 triage 或全局 kill-switch。范围检查是
协作漂移检测，不是对恶意 worker 的安全沙箱；暂停不是回滚。
账户真实执行、断网/额度耗尽试验、安装升级和实际定时接班属于下一道验收。

操作模板见 [闭环指南](recipes/shift-closed-loop.md)，底层限制见
[Repository harness](repository-harness.md)。
