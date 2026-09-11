# Hotspot article pilot

这是 `hotspot-article` 的首个端到端试运行包。主题依据 2026-07-29 前后的官方安全披露与行业进展选择。

关键结果见 `quality-report.json`：

- 机器可审计分：98/100；
- 全部 hard gates 通过；
- 相关回归：15 项通过，原始输出在 `evidence/quality-gate-test.txt`。

这里刻意没有 `publish-ready.md`。机器门和编辑自检均不等于人类主编批准；试运行稿在用户确认前保持 `draft.md` 状态。

复跑：

```bash
PYTHONPATH=src python3 -m prax.content_quality \
  examples/hotspot-article-pilot/draft.md \
  --business-bridge examples/hotspot-article-pilot/business-bridge.json \
  --sources examples/hotspot-article-pilot/source-index.json \
  --fact-check examples/hotspot-article-pilot/fact-check.json \
  --evidence examples/hotspot-article-pilot/evidence.json \
  --profile selected \
  --json-out examples/hotspot-article-pilot/quality-report.json
```
