# examples/knowledge-compile-demo · 真实验证 knowledge-compile 技能

> **当前状态（2026-04-22 下午）**：scaffold 就绪，真 run 暂被 gpt-5.4 代理维护阻塞。代理恢复后跑一次即可补全产出。

验证 `knowledge-compile` skill 在真 LLM 下是否：

1. 把 6 个散装 markdown 压成 Obsidian wiki
2. `index.md` / `daily-digest.md` / `topics/<slug>.md` 三件套齐全
3. 内部用 Obsidian 双链 `[[...]]`（不是普通 markdown `[](.)` 链接）
4. 不删原文
5. `daily-digest.md` 一屏可见（≤ 60 行）
6. 主题数在 3-7 之间（防退化成标签）

## 8 条硬契约（`assertions.sh`）

1. `index.md` 存在非空
2. `daily-digest.md` 存在且 ≤ 60 行
3. `topics/` 目录里 **2-7** 个主题
4. 至少一处 `[[...]]` 双链引用
5. 6 个原 `.md` 都没被删
6. 工作树只在 vault 目录内写入

## 跑法（代理恢复后）

```bash
cd examples/knowledge-compile-demo
./replay.sh

cd sandbox
export OPENAI_API_KEY=...
prax prompt "对 .prax/vault/ai-news-hub/2026-04-21/ 跑 knowledge-compile 技能，产出 index.md、topics/、daily-digest.md" 2>&1 | tee ../run.log

cd .. && ./assertions.sh
```

## 对比 release-notes-demo

- release-notes 必须 `--permission-mode danger-full-access` 才能 `git log` 拿 commit body
- knowledge-compile **可以用默认 `workspace-write`**——纯读 md + 写 md，不需要 shell

## 期望产出（代理恢复后补）

- `expected-index.md`、`expected-daily-digest.md`、`expected-topics/` 会像 release-notes-demo 一样 commit 进来作为真实参照
