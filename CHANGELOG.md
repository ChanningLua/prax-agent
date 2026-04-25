# Changelog

All notable changes to Prax will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.3] - 2026-04-25

### Fixed
- **`wechat_personal` first-push UX gap.** iLink doesn't let a bot push
  to a user_id it has never received a message from — the first
  `prax wechat send` after `prax wechat login` failed with the cryptic
  `iLink sendmessage error: ret=-2 errcode=None errmsg='unknown'` and
  no hint about the cause. Two surface fixes:
  - `prax wechat login` now prints a clear "one-time step" notice after
    success: open WeChat, find the bot in your contacts, send it any
    message, then `prax wechat send` will work.
  - `send_text` translates `ret=-2` into a Chinese-readable hint with
    the same fix.
  - The underlying iLink limitation is unchanged (Hermes uses the same
    pattern via `WEIXIN_HOME_CHANNEL`); we just stopped letting users
    bounce off it without a recovery path.

## [0.5.2] - 2026-04-25

### Added
- **`wechat_personal` notify provider — push to personal WeChat accounts
  via Tencent's iLink Bot API.** Companion CLI: `prax wechat
  {login, list, send, logout}`. Workflow:
  1. `prax wechat login` runs the iLink QR-scan flow; once the user
     confirms in WeChat, credentials land at
     `~/.prax/wechat/<account_id>.json` (mode 0o600).
  2. Reference the account in `.prax/notify.yaml`:
     ```yaml
     channels:
       daily-digest:
         provider: wechat_personal
         account_id: ilink_xxxxx
         to: self          # default — sends to the logged-in account itself
     ```
  3. Any cron job with `notify_channel: daily-digest` now lands directly
     in personal WeChat — no WeCom group, no third-party SaaS, no
     ServerChan/wxpusher relay.
  - Optional dependency: install `qrcode` (`pip install qrcode`) to render
    the scan code as ASCII in the terminal; otherwise the URL is printed
    and the user can open it in a browser.
  - **License attribution:** the iLink protocol shape (endpoint paths,
    headers, message-payload structure, QR-poll state machine) is adapted
    from [hermes-agent](https://github.com/Nous-Research/hermes-agent)'s
    `gateway/platforms/weixin.py` (MIT, 2025 Nous Research). Prax
    reproduces only the push-only subset — long-poll inbound, media
    encryption, typing tickets, and the bidirectional adapter intentionally
    stay in Hermes.

### Why this matters
The "AI 信息助理" flagship use case lives or dies on whether non-developers
can receive their daily digest somewhere they actually check —
**WeChat**. WeCom (`wechat_work_webhook`, shipped in 0.5.1) covers team
chats, but personal users who don't have a WeCom org now have a first-
class path too. The iLink approach uses Tencent's official bot API rather
than scraping or third-party SaaS, so it's neither bannable nor leaks
data through someone else's relay.

### Internal
- Pre-existing test debt cleaned up: `test_getting_started_covers_install_key_and_first_prompt`
  now expects `prax providers` (the doc fix landed in 0.4.x); 
  `test_run_with_model_upgrades_no_report_raises` renamed to
  `test_run_with_model_upgrades_synthesizes_report_when_loop_skips_on_complete`
  to match the runtime's switch from "raise" to "synthesize a fallback
  report" semantics.

## [0.5.1] - 2026-04-25

### Added
- **`wechat_work_webhook` notify provider** — push notifications to a 企业微信
  (WeCom) group bot. Same YAML shape as `feishu_webhook` / `lark_webhook`:
  ```yaml
  channels:
    daily-digest:
      provider: wechat_work_webhook
      url: "${WECOM_WEBHOOK_URL}"
      default_title_prefix: "[Prax] "
  ```
  Posts a markdown message; level (`info` / `warn` / `error`) maps to
  WeCom's three font colours (`info` / `warning` / `warning` — WeCom only
  ships those). The provider checks the response body's `errcode` and
  raises a clear `RuntimeError` when WeCom rejects the call (it returns
  HTTP 200 even on logical failure, so `raise_for_status` alone is not
  enough). (`tools/notify.py`)

### Why this matters
WeChat reach is the missing piece for non-developer users — most of the
audience for the upcoming `praxdaily` flagship app lives in WeChat, not
Feishu/Lark. Personal WeChat still has no stable official API, but the
WeCom group-bot is officially supported, free, and a 60-second setup. With
this provider in 0.5.1, `praxdaily` can ship its WeChat push channel
without forking notify logic.

## [0.5.0] - 2026-04-25

### Changed (Breaking behaviour)
- **Background tasks now survive the parent `prax prompt` process.** Previously
  `StartTask` scheduled work via `asyncio.create_task`, which died with the
  process that started it — meaning the "background" was effectively a fiction
  in the standalone CLI. The tool now spawns a detached OS subprocess
  (`python -m prax._background_runner`) with `start_new_session=True` on
  POSIX (`CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS` on Windows), writes
  the PID to `.prax/tasks/<task_id>.json`, and streams the agent run into
  `.prax/logs/background/<task_id>-<ts>.log`. The task JSON gains new v2
  fields — `cwd`, `pid`, `started_at`, `heartbeat_at`, `exit_code` — while
  remaining backwards compatible with v1 records on disk.
  (`tools/background_task.py`, `core/background_store.py`, new
  `_background_runner.py`)

### Added
- `CheckTask` now reconciles silent runner crashes: if the task JSON still
  says `running` but `os.kill(pid, 0)` shows the subprocess is gone, it
  flips the state to `error` with `exit_code=-1` so callers don't poll a
  permanently-stale "running".
- `CancelTask` sends `SIGTERM` to the detached subprocess (best-effort) and
  reports `signalled: true/false` in the result so the caller knows whether
  the OS accepted the cancellation.
- New optional `PRAX_BIN` env var lets operators pin which `prax`
  executable the background runner invokes for the inner agent call; by
  default it picks the first `prax` on PATH, falling back to `python -m prax`.

### Why this matters
This is the first of four milestones (M1–M4) spelled out in the audit plan
that brings Prax's real capabilities in line with the public-facing "24/7"
promise. Cron + Notify were already production-ready; after this release,
**background tasks are too**. Upcoming releases add cron job dependencies
(0.5.1), Linux auto-crontab (0.5.2), and Windows Task Scheduler (0.5.3).

## [0.4.2] - 2026-04-24

### Fixed
- **Persistent memory now actually persists in `prax prompt` mode.** The
  `MemoryExtractionMiddleware.after_model` hook scheduled extraction as a
  fire-and-forget `asyncio.create_task`; the shared httpx client was then
  closed in `_execute`'s `finally` before the task reached `store.save()`,
  so every `prax prompt` run silently produced the warning `Memory
  extraction failed: Cannot send a request, as the client has been closed`
  and wrote nothing to `.prax/memory.json`. `_execute` now drains the
  pending extraction (bounded by a 15 s timeout so a stuck LLM cannot hang
  CLI exit) before closing the client.
  (`core/memory_middleware.py`, `main.py`)
- **Extraction now uses streaming transport** when the provider declares
  `supports_streaming: true`. Some OpenAI-compatible proxies (e.g. Codex
  relays) reject non-streaming `chat/completions` with `400 Stream must be
  set to true`; the main agent loop already streams in this case, but
  `_extract_and_save` and `_extract_compound` were both using the plain
  `complete()` path and therefore 400ing once the preceding race was
  fixed. Shared helper `_extraction_llm_call` picks the right transport.
  (`core/memory_middleware.py`)

## [0.4.1] - 2026-04-24

### Fixed
- **Model lookup now prefers an available provider** when the same model name
  exists in multiple providers (e.g. bundled `zhipu.glm-4-flash` without
  `ZHIPU_API_KEY` vs a user-defined provider that reuses `glm-4-flash` with a
  working key). Previously the first-scanned (bundled, uncredentialed) entry
  won, producing a confusing `RuntimeError: No configured models are currently
  available` for new users who followed the README Quick Start with a common
  model name. Fall-back behavior is unchanged when no match is available.
  (`core/model_catalog.py`, `core/llm_client.py`)
- Fixed the matching downstream bug in `LLMClient.resolve_model`: even when
  the catalog reported the user's provider as available, `resolve_model` was
  independently returning the first-scanned provider and therefore sending an
  outbound `Authorization: Bearer ` with an empty key, producing a cryptic
  `LocalProtocolError: Illegal header value b'Bearer '`. Both resolution
  paths now share the same "prefer a provider with a real api_key" rule.

### Changed
- `Docker sandbox unavailable — falling back to local sandbox` is now emitted
  at `INFO` rather than `WARNING` so first-time users without Docker don't see
  a red warning on every run. Operators who require enforcement should keep
  using `PRAX_SANDBOX_POLICY=fail_closed`, which still errors loudly.
  (`core/sandbox/provider.py`)

## [0.4.0] - 2026-04-22

### Breaking Changes
- **Package layout migrated from flat to `src/` layout.** All internal modules now live under `src/prax/`. User-facing Python imports move from `agents.*`, `core.*`, `tools.*`, `commands.*`, `workflows.*`, `tui.*`, `runtime.*`, `integrations.*` → `prax.agents.*`, `prax.core.*`, etc. The `prax` CLI entry point is unchanged. Upgrade path for anyone importing internals directly:
  ```python
  # Before (0.3.x)
  from core.llm_client import LLMClient
  from agents.base import BaseAgent
  # After (0.4.0)
  from prax.core.llm_client import LLMClient
  from prax.agents.base import BaseAgent
  ```

### Added
- **NotifyTool** (`tools/notify.py`) + `.prax/notify.yaml`: outbound notifications through named channels.
  - Providers: `feishu_webhook`, `lark_webhook`, `smtp`.
  - SMTP password must come from an env var (via `auth_env:`) — YAML never holds credentials.
  - `${VAR}` expansion works in URLs, mirroring the MCP config convention.
  - Tool rejects ad-hoc URLs by design so every destination is reviewable.
- **Cron scheduler** — cross-process scheduled tasks:
  - `prax cron list | add | remove | run | install | uninstall` subcommands.
  - `.prax/cron.yaml` schema: name, schedule (5-field crontab), prompt, session_id, model, notify_on, notify_channel.
  - Single dispatcher model: one LaunchAgent on macOS (`StartInterval=60`) or one crontab line on Linux fires `prax cron run` every minute; the dispatcher reads cron.yaml and runs due jobs.
  - Per-job logs at `.prax/logs/cron/<name>-<timestamp>.log`.
  - `notify_on: [success, failure]` + `notify_channel:` wires results into NotifyTool automatically.
  - Built-in 5-field crontab evaluator (`*`, `*/N`, `a,b,c`, `a-b`, `a-b/s`) with classic dom/dow OR-rule — no external cron dependency.
- **Bundled skills** (auto-discovered from the package via `core/skills_loader.py`):
  - `skills/browser-scrape/` — drives AutoCLI (`github.com/nashsu/AutoCLI`) over the existing Bash tool to scrape Twitter/X, Zhihu, Bilibili, Reddit, HackerNews and 50+ others while reusing the user's Chrome login.
  - `skills/knowledge-compile/` — turns a folder of raw markdown into an Obsidian-ready wiki: `index.md` + `topics/<slug>.md` + one-screen `daily-digest.md`. Hard contracts: Obsidian `[[...]]` double-links, no deletion of source files, 3–7 topics.
  - `skills/ai-news-daily/` — end-to-end pipeline stitching the three skills above together.
- **Recipe docs** shipped with the npm tarball:
  - `docs/recipes/browser-autocli.md` — AutoCLI install guide.
  - `docs/recipes/ai-news-daily.md` — one-page setup recipe (5 commands).
  - `docs/recipes/ai-news-daily/research-analyst.md` — reference agent spec to copy into a project's `.prax/agents/`.

### Changed
- `core/cron_installer.py` now resolves the dispatcher argv robustly: `PRAX_BIN` env override, then `shutil.which("prax")`, finally `sys.executable -m prax`. LaunchAgent plists also forward `PATH` and (when set) `PYTHONPATH` so npm-installed prax keeps working under launchd.
- `prax --help` and the zero-arg usage text now list the `cron` subcommand.
- `UnknownJobError` prints `cron job 'name' not found` instead of just `'name'`.
- `NotifyTool.description` explicitly tells the model that channels must be declared in `notify.yaml` before use.
- `package.json` files list now includes `docs/recipes/` so recipe docs ship with the npm package.

### Fixed
- `core/config_files.py` — `load_rules_config` no longer returns `None` for empty `.prax/rules.yaml` files. Regression from a very short window in 0.3.1; already addressed in 0.3.2, reaffirmed here.

### Out of Scope (planned for 0.5+)
- Skill marketplace / install commands (`prax skill install github:...`)
- Windows Task Scheduler integration for cron
- `wechat_work_webhook` provider for enterprise WeChat
- Auto-editing the user's Linux crontab (deliberately print-only for now)

## [0.2.0] - 2026-04-17

### Added
- Claude Code integration enhancements:
  - MCP memory server for persistent context across sessions
  - Pre-commit quality check hook
  - Pre-write secret scan hook
- New benchmark results visualization with improved design
- Redesigned integration paths diagram with consistent visual style
- npm package support with JavaScript wrapper
- Centralized JSON-schema validation for tool inputs before execution
- Workspace boundary enforcement for file-writing tools
- Atomic JSON writes plus schema-version markers for session, background-task, and memory state files

### Changed
- README structure: reordered sections (Quick Start → Why Prax → Usage → Results → Integration Paths)
- Fixed architecture terminology: "Scanner → Iterate → Verify" → "test-verify-fix loops"
- Optimized benchmark data presentation (10/10 success rate, 49% faster)
- Updated both English and Chinese documentation
- Delegated subagents now inherit the parent permission mode instead of forcing full access
- Repository documentation now matches the current provider/configuration model

### Fixed
- Command names in README: `prax install-claude` → `prax /init-models claude`, `prax doctor-claude` → `prax /doctor claude`
- Benchmark chart layout issues (title overlap)
- Integration paths diagram visual consistency with other diagrams

## [0.1.0] - 2026-04-12

### Added
- Initial release
- Natural language task execution
- Multi-model support (Claude, GPT, Gemini)
- Persistent memory system (local/sqlite/openviking)
- 14 built-in tools + MCP extensibility
- Permission control (read-only/workspace-write/danger-full-access)
- Agent orchestration (Ralph, Sisyphus, Team)
- REPL mode with slash commands
- Error recovery and retry logic
- Session management and resumption
