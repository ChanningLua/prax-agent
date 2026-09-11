# Shift 闭环优化 · 2026-09-10

本轮在 `codex/integrate-local-prax` 的目标工作区继续开发，未修改来源
`Documents/prax 2/remote-prax` 的项目代码，未提交、推送或更改其他项目。
测试通过显式绝对 PYTHONPATH 加载目标 `src/prax`，已有虚拟环境仅作为
Python/pytest 工具来源，不进行安装或环境修改。

本文区分首次闭环实现与随后审查修复；下方原有测试总数是上一阶段快照，
不能当作本次七项修复后的全量验证结果。

## 完成的工程闭环

- `shift plan/run/resume/status/pause` 与 repository harness 共用执行内核，
  CLI 帮助暴露新命令，旧 goal/feature 入口保留并标明不同保证。
- task/profile、分支/HEAD/index、工作区检查点和验收控制文件绑定到固定 ID；
  改目标、验收、worker、预算或代码身份时拒绝复用旧结果。
- 同仓库互斥、监督进程组、累计墙钟/派工预算、持久暂停。定时续跑不能
  解除暂停或重置额度；启动与清理时间也计入总墙钟预算。
- 子进程必须提交本次 execution ID 的新结果，exit 0 不能复用过期 PASS；
  启动失败记录 HARNESS-ERROR，丢失 identity 的旧证据不能被重新绑定。
- pytest 的明确文件、选择器、目录及附近配置会形成指纹；额外 fixture/
  helper 可用 `verifier.control_files` 显式冻结，避免验收被改弱。
- cron Shift 要求 `--task`，保持同一 ID；只有匹配任务的结构化 verified
  PASS 和 exit 0 才成功；相同结果通知去重，发送失败可在后续调度重试。
- Claude/Codex 错误协议事件会失败关闭；Claude 非零退出不再被吞掉，
  stderr 并发排空并限制保留尾部大小，两个 step adapter 都使用新上下文。
- Claude 默认从 bypassPermissions 改为 dontAsk；模型选择不再在任务契约
  路径里静默忽略。Codex 保持 workspace-write。
- 随包 Claude/Codex 技能更新为“协助整理契约 → 计划 → 执行 → 查看/
  暂停/恢复”；技能校验通过。未更新本机已安装插件缓存。
- README、当前规范和实用指南同步；旧路线图保留在 history 中，避免把
  历史设想、兼容接口与实际实现混在一起。

旧 OrchestratorLoop 额外修正跨 goal 复用及缓存 PASS 不重验的问题，
但并未因此具备新任务路径的全部代码身份与监督保证。

## 审查后的七项修复

1. **cron 日志不再污染代码检查点。** 将 `.prax/logs/cron/` 精确列为
   运行时输出；不排除整个 `.prax/`。任务、profile、cron 配置及其他
   日志仍受原有漂移检查约束。真实 CLI 连续两次 cron tick 的回归覆盖
   “第一轮写日志，第二轮继续同一任务”，不要求用户放宽 scope。
2. **监督器死亡后的进程与锁所有权。** 普通 TERM/HUP/INT 进入进程组清理，
   清理期间的首次或重复信号不能打断最终 KILL/wait；
   子运行时另持父进程专有管道的读端，通过 EOF 监测父进程死亡并终止
   专用运行时进程组。运行时继承仓库/运行锁 FD，避免父进程先退出时
   提前释放协作锁；这些 FD 不再传给任意 worker/验收子命令。父进程被
   强杀时可能留下 RUNNING，供人工检查；不伪造完成状态或自动重置预算。
3. **宿主错误后的合法部分修改可保留。** worker 修改了授权范围内的
   代码后报错，不再一概将该修改变成无法续跑的漂移。只有控制文件、
   仓库身份与范围检查均通过，且生命周期释放未改变代码，才记录受保护
   检查点；结果仍是失败而非 PASS。恢复先独立验收，并沿用原有派工及
   墙钟预算。越界或验收控制漂移仍拒绝自动接班。
4. **生命周期回调不能替换被验收的代码。** adapter `check` 与 `release`
   前后均比较严格代码快照和控制指纹；release 前还核对真正通过验收的
   快照。即使改动位于授权 scope，也不能在验收后悄悄替换 source，保留
   PASS 标签。发现变化时降为 HARNESS-ERROR，并保留所有权检查提示。
   验收命令本身也必须保持前后源码一致；边界检查失败的变化不会被
   finally 接纳为新的可恢复检查点。
5. **没有实际执行测试不再等同通过。** 对已识别的 pytest、unittest
   等 runner，明确的 collection/list-only 模式及标准输出中的全跳过、
   零执行证据不再视作 PASS，而作为验收环境问题停止，不直接进入修复
   派工。未知自定义 reporter 和其他可信脚本仍按其退出码契约解释；
   本次不是通用测试报告协议，也不能从未知输出证明测试确实执行过。
6. **缺通知 channel 不能确认投递。** 找不到配置的 channel 时明确失败，
   不写入“已投递”去重状态；补好配置后，相同结果可在下一次既有调度
   机会重试。成功投递后仍去重，不在本次修复中创建额外调度或真实通知。
7. **旧 Shift cron 配置可管理但不可执行。** 已保存、缺 task 的旧
   prompt-only orchestrate 任务可 list/remove，且不阻断同配置内有效
   任务的添加、管理和运行。列表显示 `migration-required`；到期时返回
   禁用原因而不调用 worker。新增任务仍严格要求契约；迁移需要用户明确
   准备 goal、scope、verifier、budget，再 remove/re-add，系统不臆造验收。

### 本次修复的验证状态与边界

cron/store/contract 定向回归已完成，覆盖两次真实 CLI 调度、精确日志
排除、缺 channel 后恢复投递、旧任务可管理与拒绝执行。真实 CLI 用的是
临时仓库内预先通过的 unittest，结果 `iterations=0`，没有调用真实模型。
通知发送使用替身 provider，没有访问账户或发送外部消息。

进程 TERM/KILL、保锁、宿主错误后的续跑、生命周期改写与零执行验收已
加入针对性回归。最终结果：

- 全部 unit + `test_main_full.py` + `test_openprax_claude_code.py`：
  **2529 passed / 6 failed**，164.58 秒。
- integration + 其余 10 个顶层文件：**172 passed**，1.05 秒。
- 两组文件无重叠，合计 **2701 passed / 6 failed**，相对上一阶段增加
  60 项通过的回归测试；本轮七项修复相关测试没有失败。
- 定向集 **223 passed**（112.84 秒）；独立检查点负例 **3 passed**
  （5.67 秒）。父管道端点清理最终调整后，进程/锁/检查点/释放相关再测
  **25 passed**（39.22 秒），覆盖 TERM/HUP/INT 清理信号。这些均与
  上述宽范围结果重叠，不重复累加。
- `git diff --check` 和修改代码/测试的 Ruff 致命错误规则
  `E9,F63,F7,F82` 通过；未宣称完整风格 lint 全绿。

6 项失败仍是已在旧基线复现的 LangGraph/BeautifulSoup/Textual 可选
依赖与测试问题，失败名称和原因未新增；本轮未安装或改动这些依赖。
两组测试各有 3 条现有事件循环/未 await mock 警告。测试中的进程仅属于
临时离线夹具，没有终止用户正在工作的其他进程。

## 上一阶段的验证方式与结果

全部测试使用临时 Git 仓库、假 worker 或本地验收命令；没有访问真实
模型账户、设备、生产系统，没有创建实际调度或发送通知。

- 实际 `python -m prax shift` CLI → supervisor → child → verifier 的 PASS
  路径通过；已经通过的验收为零 worker 调用，二次续跑复用同一结果。
- 实际 CLI 的环境失败 → 暂停 → 定时续跑仍暂停 → 显式恢复路径通过；
  未通过验收时不会返回成功退出码，也不会自动补充预算。
- 假 worker 回归覆盖修改后独立验证、验收失效、任务/代码/控制漂移、
  并发锁、派工/墙钟预算、协议失败、暂停检查点、陈旧结果与缺失身份。
- `git diff --check` 通过；改动运行时代码与测试的 Ruff 致命错误规则
  `E9,F63,F7,F82` 通过。完整风格 lint 仍有风格诊断，不宣称全绿。

首次闭环实现完成后的回归快照（七项审查修复之前）：

- 全部 unit + `test_main_full.py` + `test_openprax_claude_code.py`：
  **2469 passed / 6 failed**，219.01 秒。
- integration + 其余 10 个顶层测试文件：**172 passed**，2.18 秒。
- 上述两组无测试文件重叠，合计 **2641 passed / 6 failed**。
- 新入口与 cron 装配额外定向复测 **35 passed**，已包含在宽范围结果中，
  不重复累加。随包两个技能均通过 quick_validate。

6 项失败是首次整合已在基线复现的可选依赖问题：4 个 LangGraph mock、
1 个 BeautifulSoup 符号、1 个缺失 Textual 的 TUI 测试；本轮未改变这些模块。
该阶段闭环相关回归没有剩余失败，但整个仓库测试不能称为全绿。

## 仍不代表什么

这是一条低风险、显式 code-check 任务的闭环，不是通用全天自治系统。
没有自动发现/准入新任务、自动生成预算、自动发布、push、merge、全局
kill-switch 或学习型自治策略。缓存 PASS 也不是外部服务的实时健康检查。

Claude dontAsk 与 Codex workspace-write 不是等价安全沙箱；合作式 scope
守卫不是对恶意 worker 的隔离。暂停保留部分修改，不执行回滚。
真实账户、额度/断网恢复、项目授权、安装升级和定时实测仍是下一道验收。
不能把本轮离线测试描述为已经完成本机 7×24 在线运行。

宿主行为参考：[OpenAI 非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)、
[Claude 权限模式](https://code.claude.com/docs/en/permission-modes)。
