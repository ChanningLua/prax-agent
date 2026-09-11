# 一天十篇：批量文章流水线

这条流水线把开源采集和研究工具放在适合的位置：

```text
TrendRadar / AutoCLI
        ↓ 热点与需求线索
三轨评分 + 技术到业务桥
        ↓ 十个隔离工作区
Crawl4AI / 公开 HTTP
        ↓ 原始正文
GPT Researcher / STORM
        ↓ 研究线索与追问
Prax hotspot-article
        ↓ 来源、实测、事实和文风质量门
草稿或发布候选
```

GPT Researcher 和 STORM 不直接决定正文事实。它们提供研究问题和线索，文章仍需回到原始 URL、代码、论文和实际验证。

## 1. 查看当前可用能力

```bash
python3 -m prax.article_integrations probe
```

状态必须区分：

- `READY`：依赖和运行条件齐全；
- `MISSING`：适配器存在，但本机缺依赖、凭据或浏览器扩展。

## 2. 接 TrendRadar

TrendRadar 负责公开热榜和 RSS 聚合。Prax 不复制其代码，直接读取它生成的 Markdown、JSON 或 SQLite：

```bash
export TRENDRADAR_OUTPUT=/path/to/TrendRadar/output

python3 -m prax.article_integrations ingest-trendradar \
  "$TRENDRADAR_OUTPUT" \
  --json-out .prax/vault/ai-news-hub/$(date +%F)/raw/trendradar.json
```

没有 URL 的热榜项只能用于发起搜索，不能成为引用来源。

## 3. 接 AutoCLI

AutoCLI 用于需要现有 Chrome 登录态的平台。二进制和 Chrome 扩展都正常时，`probe` 才会显示 `READY`。

```bash
autocli doctor

python3 -m prax.article_integrations collect-autocli twitter-search \
  --query "AI Agent" \
  --limit 30 \
  --json-out .prax/vault/ai-news-hub/$(date +%F)/raw/x-agent.json
```

如果二进制已经放在 `.venv/bin/autocli`，但 doctor 显示扩展未连接：打开 `chrome://extensions`，开启开发者模式，选择“加载已解压的扩展”，指向 `.venv/share/autocli/extension`。这是一次会改变 Chrome 扩展状态的人工操作，流水线不会替用户静默完成。

不通过 AutoCLI 发帖、点赞或关注。

## 4. 抓文章正文

装有 Crawl4AI 时优先使用它：

```bash
pip install "crawl4ai==0.9.2"
crawl4ai-setup
```

然后：

```bash
python3 -m prax.article_integrations fetch \
  "https://example.com/original" \
  --out research/source-01.md
```

未安装 Crawl4AI 时，`auto` 模式退回公开 HTTP 抓取，遵守 `robots.txt`，并拒绝本地、私有和链路本地地址。公开发布或分发集成时，按 Crawl4AI 的许可证要求保留归属说明。

## 5. 可选深度研究后端

GPT Researcher：

```bash
pip install gpt-researcher
export OPENAI_API_KEY=...
export TAVILY_API_KEY=...

python3 -m prax.article_integrations research \
  "主题、业务决策和研究问题" \
  --out research-leads.md
```

STORM 不嵌进 Prax 主进程。已有 STORM 研究结果时：

```bash
export STORM_OUTPUT=/path/to/storm-output
```

批次技能读取其中报告补充视角。没有模型和检索凭据时，两个后端都明确显示 `MISSING`，不会伪装成已经研究。

## 6. 创建十篇批次

先准备至少 12 个完整候选，再执行：

```bash
python3 -m prax.article_batch \
  topic-candidates.json \
  --count 10 \
  --date "$(date +%F)" \
  --out-root ".prax/vault/article-batches/$(date +%F)/run"
```

批次必须满足：

- `complete: true`；
- `calibration-gate.json` 已创建，首篇以外的目录先处于 `waiting-for-calibration`；
- `event 3 / demand 5 / evergreen 2`，或配置中的等价比例；
- 十个独立工作区；
- 每篇都有 `business-bridge.json`、`research-plan.json` 和 `status.json`。

先把一篇完整写完，用真实成稿修正选题重量、来源、证据、文风和质量规则。按 `selected-analysis`、`selected-framework`、`selected-hands-on` 三条路线选择质量门；三类都保持 5–9 个大章节，但证据形态和篇幅不同。校准时还要检查腾讯云近 30 天精选榜，撞题且没有更强原创证据的稿件直接换题。`calibration-gate.json` 释放后，再按 `article-batch` 技能最多两篇并行推进。十个标题不等于十篇文章；只有完成研究、实测、事实核验和质量门的目录才能进入 `ready`。
