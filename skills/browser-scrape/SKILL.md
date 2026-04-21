---
name: browser-scrape
description: 用 AutoCLI 二进制驱动用户已登录的 Chrome 抓取 Twitter/X、知乎、Bilibili、Reddit 等 55+ 站点
allowed-tools: [Bash, Write, Read, Glob]
triggers: [抓取, 爬取, 登录态, Chrome, scrape, 推文, timeline, hot, 热榜, zhihu, bilibili, twitter, X, reddit, autocli]
tags: [browser, scraping, data-collection, autocli]
priority: 7
---

# 浏览器抓取技能（AutoCLI）

复用用户本机 Chrome 的登录态抓取需要登录的站点（推特/X、知乎、Bilibili、Reddit、小红书等 55+ 平台）。不需要 cookie 配置、不需要 API key。

## 前置要求（由用户完成一次即可）

1. 安装 AutoCLI Rust 二进制：参见 https://github.com/nashsu/AutoCLI（单文件 ~4.7MB，无运行时依赖）
2. 装 autocli Chrome 扩展（仓库 README 里有下载链接和加载步骤）
3. 保持 Chrome 运行并在目标站点处于登录态
4. `autocli doctor` 应报告 `OK`

如果 `autocli doctor` 失败，先提醒用户修复前置条件，不要继续抓取。

## 能力范围

AutoCLI 在 Prax 里通过普通 `Bash` 工具调用——它不是 MCP server，只是一个 CLI。只要 PATH 里能找到 `autocli`，任何 Prax agent 都能用。

## 常用命令（按频次排序）

```bash
# 诊断（第一次使用必跑）
autocli doctor

# 推特/X
autocli twitter timeline --limit 20 --format json
autocli twitter search --query "AI safety" --limit 10 --format json

# 知乎
autocli zhihu hot --limit 20 --format json

# Bilibili
autocli bilibili hot --limit 10 --format json

# Reddit
autocli reddit subreddit --name "MachineLearning" --limit 15 --format json

# 任意网页文章（不走登录态）
autocli read https://example.com/article --format md
autocli read https://example.com/article --format text -o /tmp/article.txt
```

- `--format json|md|text|yaml|csv` 任选；编程任务优先 `json`，归档任务用 `md`
- `--limit N` 控制条数
- `autocli --help` 查所有子命令；单平台用 `autocli twitter --help`

## 典型抓取流程

用户让我"抓今天 X 上 AI 相关点赞 top 10 存到 Obsidian"，我的动作：

1. `autocli doctor` 确认前置条件
2. `autocli twitter timeline --limit 50 --format json` 拉最近推文
3. 本地过滤（按关键词/点赞数），用 Write 工具存到 `.prax/vault/ai-news-hub/YYYY-MM-DD/` 下
4. 每条一个 markdown 文件，header 带 `tweet_id / author / likes / url / scraped_at`
5. 完成后总结文件路径给用户

## 产出约定（配合 knowledge-compile 技能）

- **目录命名**：`.prax/vault/<topic>/YYYY-MM-DD/`（方便 Obsidian 按日期归档）
- **文件命名**：`<source>-<id>.md`，例如 `twitter-17xxx.md`
- **文件 frontmatter**：至少包含 `source / url / scraped_at`，供下游 `knowledge-compile` 编译 wiki 用
- **原始 JSON 保留**：把 `autocli ... --format json` 的原始输出同步存一份到 `raw/<source>-<stamp>.json`，便于溯源

## 边界与禁区

- 不要用 AutoCLI 做 **写操作**（发帖/点赞/关注）除非用户明确要求；无脑批量操作会导致账号封禁
- 不要把登录 cookie 或扩展私钥外传
- 被站点频控或返回异常时先停下来报告，不要循环重试——可能会触发风控

## 配合其他技能

- 抓完 → `knowledge-compile` 把散文件整理成 wiki
- 每天定时运行 → `cron` 里配 `prax cron add` 用本技能
- 出成品 → `Notify` 工具把结果推到飞书/邮箱
