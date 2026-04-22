# examples/support-digest-demo · 真实验证 support-digest 技能

> **当前状态（2026-04-22 下午）**：scaffold 已就绪；**真 run 暂被上游代理维护阻塞**（"服务器当前正在维护中，预计半小时完成"）。脚手架本身已 smoke 过——`replay.sh` 正确落下 8 条带 PII 的 fixture，`assertions.sh` 在"未跑"状态下正确 FAIL。代理恢复后再跑一次即可补全产出。

与 `release-notes-demo/` 结构同款。要验证的是 **support-digest skill 在真 LLM 下是否真的**：

1. 读 `.prax/inbox/tickets-<date>.json`
2. **先** 脱敏 email / phone / 银行卡号（契约最核心的一条）
3. 按 category 和 severity 聚类，产 5 条 highlights（上限，不可超）
4. 写 `digest.md` 和 `tickets-redacted.json` 到 `.prax/vault/support/<date>/`
5. 把原文件 mv 到 `.prax/inbox/archive/`
6. **工作树**只碰 `.prax/` 下的东西，不污染仓库别处

## 6 条硬契约（`assertions.sh`）

| # | 契约 |
|---|---|
| 1 | `.prax/vault/support/<date>/digest.md` 存在且非空 |
| 2 | digest 里有"亮点/highlights/Top"段，且**≤5** 条 |
| 3 | digest 里**不含**任何原始 email（`jane.doe@example.com` 等） |
| 4 | `tickets-redacted.json` 存在且同样脱了敏 |
| 5 | 原 ticket 文件已被 mv 到 `.prax/inbox/archive/`（原位置无此文件） |
| 6 | 工作树写入**只在 `.prax/` 下**（不动仓库其他文件） |

## 跑法（代理恢复后）

```bash
cd examples/support-digest-demo
./replay.sh

cd sandbox
export OPENAI_API_KEY=...
prax prompt --permission-mode danger-full-access "触发 support-digest 技能处理 .prax/inbox/tickets-2026-04-21.json" 2>&1 | tee ../run.log

cd .. && ./assertions.sh
```

`--permission-mode danger-full-access` 是因为 Step 5 要跑 `mv` 把原文件归档——Prax 的 Bash 工具默认被 workspace-write 挡住。这是一个已知 UX 毛刺，在未来的 Prax 版本里会加一个 "safe mv in workspace" 工具缓解。

## 期望产出（代理恢复后补）

跑通后 `expected-digest.md` 和 `expected-redacted.json` 会像 `release-notes-demo/` 一样被 commit，用作 tutorial"应该看到"段的真实参照。

## 失败模式记录

**第一次未完成跑（2026-04-22 上午，gpt-5.4）**：
- 现象：prax 进程启动正常，打印 session id 后就 silent 退出
- 根因：上游代理维护中，返回非 SSE 格式的维护提示（`{"success": false, "message": "...维护中..."}`）
- Prax 的 openai format 解析器对非 stream 的维护消息没处理，吃掉了错误
- **值得改的 runtime 项**：把 non-stream JSON 错误响应识别为"upstream failure"并抛给 CLI 打印。留给下一轮优化。
