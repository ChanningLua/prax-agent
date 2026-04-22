# Recipe · AI 日报自动化 Pipeline

复刻"Hermes + AutoCLI + Obsidian"那篇文章的自动知识库工作流，用 Prax 完成：

> 每天 17:00，自动抓 X / 知乎 / Bilibili / HackerNews 的 AI 相关内容 → 整理成 Obsidian 风格 wiki → 推送一屏可见的简报到飞书/邮箱。

用户一次性配置好，后续完全托管，不用再开终端。

## 总览

```
                ┌─────────────────┐
                │   cron 17:00    │
                └────────┬────────┘
                         │
                ┌────────▼────────┐
                │  prax cron run  │
                └────────┬────────┘
                         │
        ┌────────────────┴────────────────┐
        │    research-analyst agent       │
        │    (触发 ai-news-daily 技能)     │
        └────────────────┬────────────────┘
                         │
    ┌──────┬─────────────┼──────────────┐
    │      │             │              │
    ▼      ▼             ▼              ▼
 autocli autocli      autocli       autocli
 twitter  zhihu      bilibili     hackernews
    │      │             │              │
    └──────┴──────┬──────┴──────────────┘
                 │
         .prax/vault/ai-news-hub/
             2026-04-22/*.md
                 │
                 ▼
        knowledge-compile 技能
        (index.md / topics/ / daily-digest.md)
                 │
                 ▼
        Notify → 飞书 webhook
```

## 第一步：安装前置

```bash
# 1. 装 prax 本身
npm install -g praxagent

# 2. 装 AutoCLI 二进制 + Chrome 扩展
# 跟随仓库 README：https://github.com/nashsu/AutoCLI
# 装好后：
autocli doctor   # 应当全绿

# 3. 准备 Chrome 登录态
# 打开 Chrome，登录 X / 知乎 / Bilibili
```

## 第二步：配置出站通道（飞书示例）

项目根目录（或 `~/.prax/`）下 `.prax/notify.yaml`：

```yaml
channels:
  daily-digest:
    provider: feishu_webhook
    url: "${FEISHU_WEBHOOK_URL}"
    default_title_prefix: "[Prax] "
```

导出 webhook：

```bash
# 飞书群 → 设置 → 群机器人 → 添加机器人 → 自定义 webhook
export FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxx
```

验证（手动发一条）：

```bash
prax prompt "调 Notify 工具给 daily-digest 发一条 title=Ping body=hello"
```

飞书群里应该能收到。

## 第三步：可选 —— 拷贝 research-analyst agent spec

`ai-news-daily` 技能本身已经够用，但如果想让 Prax 自动路由到这个 agent（而不是默认 agent），复制一份 agent spec：

```bash
mkdir -p .prax/agents
cp <prax-install-dir>/docs/recipes/ai-news-daily/research-analyst.md \
   .prax/agents/research-analyst.md
```

查仓库中样本文件位置：

```bash
python3 -c "import prax; import os; print(os.path.join(os.path.dirname(prax.__file__), 'docs/recipes/ai-news-daily/research-analyst.md'))"
```

**如果不想拷**，下一步直接把 prompt 写清楚（让 Prax 从关键词触发 `ai-news-daily` 技能即可，效果一样）。

## 第四步：挂 cron

```bash
prax cron add \
  --name ai-news-daily \
  --schedule "0 17 * * *" \
  --prompt "触发 ai-news-daily 技能，生成今日 AI 简报并推送到 daily-digest" \
  --session-id cron-ai-news \
  --notify-on failure \
  --notify-channel daily-digest
```

查看：

```bash
prax cron list
```

## 第五步：安装调度器

```bash
prax cron install
```

macOS 会在 `~/Library/LaunchAgents/dev.prax.cron.dispatcher.plist` 写一个 LaunchAgent，每分钟触发一次 `prax cron run`，由 dispatcher 判定哪些 job 到期。

Linux 会打印一行 crontab，要你手动 `crontab -e` 加上（Prax 不自动改 crontab）。

**注意**：如果你装 prax 用的是 npm，LaunchAgent 用的是 `python -m prax`，启动时可能找不到 prax 所在的 Python。两个解法：

1. 手编辑 plist 把 `PATH` 加上 `/opt/homebrew/bin:/usr/local/bin`
2. 或 `export PRAX_BIN=/opt/homebrew/bin/prax`（任意 prax 可执行绝对路径），然后重装：`prax cron install`

## 第六步：验证

手动跑一次，不等 17:00：

```bash
prax cron run
```

看日志：

```bash
ls -l .prax/logs/cron/
cat .prax/logs/cron/ai-news-daily-*.log
```

看产出：

```bash
ls -la .prax/vault/ai-news-hub/$(date +%F)/
cat .prax/vault/ai-news-hub/$(date +%F)/daily-digest.md
```

飞书群应该收到一张卡片，标题 `[Prax] AI 日报 · 2026-04-22`。

## 常见问题

**Q: LaunchAgent 说 `autocli: command not found`？**
A: LaunchAgent 的 PATH 和终端不同。把 autocli 放 `/usr/local/bin/` 或在 plist 里改 `EnvironmentVariables.PATH`。

**Q: 每天推文被重复收录？**
A: session_id 固定 + 日期目录隔离，所以不会。但如果你 `cron list` 里有两条一样的 job，就会跑两次——删掉一条即可。

**Q: 推到飞书失败？**
A: 先验 `export FEISHU_WEBHOOK_URL=...`；再手动跑 `prax prompt "调 Notify ..."`；最后看 `.prax/logs/cron/*.log` 里 Notify 工具的返回内容。

**Q: 要换成邮件而不是飞书？**
A: `.prax/notify.yaml` 里加一个 smtp channel，然后 cron job 的 `--notify-channel` 改个名字，改动就这两处。

## 要改什么

- 换抓取源：编辑 `skills/ai-news-daily/SKILL.md` 的 Step 2
- 换主题关键词：编辑 Step 3
- 换产出布局：编辑 `skills/knowledge-compile/SKILL.md`
- 换频次：`prax cron remove --name ai-news-daily && prax cron add ... --schedule "0 9,17 * * *"`

所有流程就 3 个 skill 加 4 个配置文件，改起来都是 markdown / yaml，不用碰 Python。
