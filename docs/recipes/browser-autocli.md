# 让 Prax 抓 Twitter / Bilibili / 知乎 等登录站（AutoCLI 接入）

目标：让 `prax` 能跑一条命令就把 X 上与 AI 相关的推文、知乎热榜、Bilibili 热门等抓进本地仓库，用户的 Chrome 登录态直接复用，不配 cookie、不申请 API key。

## 一、一次性安装（由用户完成）

1. 下载 AutoCLI 二进制（Rust 单文件，~4.7MB）：
   - 仓库：<https://github.com/nashsu/AutoCLI>
   - Release 页面选对应平台的二进制，放到 PATH，例如 `~/bin/autocli` 并 `chmod +x`
2. 安装 autocli Chrome 扩展（仓库 README 有详细步骤）
3. 打开 Chrome，登录目标站点（X / 知乎 / Bilibili / ...）
4. 跑诊断：
   ```bash
   autocli doctor
   ```
   全部 `OK` 即可进入下一步。

## 二、验证 Prax 能调到 AutoCLI

Prax 不需要任何额外配置就能调用 `autocli`——它就是一个普通的 CLI 程序，`prax` agent 经 `Bash` 工具就能跑。不需要 MCP server，也不需要 skill 安装。

本仓库自带的 `skills/browser-scrape/SKILL.md` 会在 Prax 启动时自动注入提示，agent 看到"抓 X / 知乎 / 推文 / 登录态"这类关键词就会触发该 skill。

手测：

```bash
prax prompt "用 AutoCLI 抓 X 最近 20 条推文存成 json 到 /tmp/tweets.json"
```

agent 会先跑 `autocli doctor`，OK 之后直接 `autocli twitter timeline --limit 20 --format json > /tmp/tweets.json`。

## 三、每天自动抓（配合 M2 cron）

假设想复刻 Hermes 那篇文章的效果——每天下午 5 点抓 X 推文入库：

```bash
prax cron add \
  --name ai-news-daily \
  --schedule "0 17 * * *" \
  --prompt "触发 browser-scrape 技能：抓 X 最近 2 小时与 AI 相关的点赞 top 10 推文，每条存成 markdown 到 .prax/vault/ai-news-hub/\$(date +%F)/twitter-<id>.md" \
  --session-id cron-ai-news \
  --notify-on success failure \
  --notify-channel daily-digest

prax cron install   # macOS 写 LaunchAgent；Linux 给你一行 crontab
```

到点就会跑。日志在 `.prax/logs/cron/ai-news-daily-<stamp>.log`。

## 四、结果推送（配合 M1 notify）

`.prax/notify.yaml`：

```yaml
channels:
  daily-digest:
    provider: feishu_webhook
    url: "${FEISHU_WEBHOOK_URL}"
    default_title_prefix: "[Prax] "
```

export `FEISHU_WEBHOOK_URL=https://open.feishu.cn/...` 后，cron 成功/失败都会推一条飞书卡片。标题示例：`[Prax] cron [ai-news-daily] success`。

## 五、常见问题

| 症状 | 原因 | 解法 |
|---|---|---|
| `autocli twitter timeline` 超时 | Chrome 扩展没装 / 没授权 | 重装扩展，`autocli doctor` 要全绿 |
| 推文只有 5 条返回 | 没有登录或 session 过期 | 去 Chrome 里重新登录 X |
| cron 日志里 `autocli: command not found` | LaunchAgent 的 PATH 和终端不同 | 把 `autocli` 放到 `/usr/local/bin`，或在 plist 里改 `EnvironmentVariables.PATH` |

## 六、为什么 Prax 不内置 AutoCLI

- AutoCLI 维护节奏快，绑定版本会拖 Prax 的后腿
- 扩展需要用户侧 Chrome 操作，无论如何都是用户决策
- Prax 的边界是 runtime + orchestration；抓取的实现细节交给专门工具
- 本 skill 只做"告诉 agent 怎么用"这一层，保持 loose coupling
