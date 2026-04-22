# Changelog

All notable changes to Prax will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2026-04-22

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
