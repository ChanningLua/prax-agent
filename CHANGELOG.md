# Changelog

All notable changes to Prax will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
