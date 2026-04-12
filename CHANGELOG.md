# Changelog

All notable changes to Prax will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Centralized JSON-schema validation for tool inputs before execution
- Workspace boundary enforcement for file-writing tools
- Atomic JSON writes plus schema-version markers for session, background-task, and memory state files

### Changed
- Delegated subagents now inherit the parent permission mode instead of forcing full access
- Repository documentation now matches the current provider/configuration model

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
