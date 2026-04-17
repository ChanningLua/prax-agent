# Changelog

All notable changes to Prax will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
