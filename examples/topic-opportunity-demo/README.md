# 需求型选题示例

这个示例把一张文章列表截图转成三轨选题候选。截图是在发布约 47 分钟时取得：

- “AI 联网搜索”条目：28 阅读、6 赞、10 评论；
- “AI 时代，校招生应该怎么培养”：54 阅读、5 赞、18 评论。

评论密度只能说明“值得继续查”，不能单独证明热门或付费需求。候选还要补客户项目、重复搜索问题、工单、Issue、岗位变化等证据。

运行：

```bash
PYTHONPATH=src python3 -m prax.topic_opportunity \
  examples/topic-opportunity-demo/topic-candidates.json \
  --json-out examples/topic-opportunity-demo/opportunity-report.json \
  --bridge-out examples/topic-opportunity-demo/business-bridge.json
```

在没有历史内容时，评分器优先补 `demand` 轨道。当前示例会选“AI 联网搜索应该自建还是采购”，因为它同时有真实客户项目、明确技术决策和较高验证空间；安全事件保留在 `event` 轨道，不会因为短期热度自动挤掉需求选题。

`business-bridge.json` 不负责替作者“联想业务”。它只把候选中已经写明的来源信号、技术变化、受影响流程、业务决策、验证方法、成功指标和不适用条件带到后续研究；少一项就不能进入精选长文。
