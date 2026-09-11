# Prax

**Turn agent capability into work you can verify.**

An open-source runtime for agent-driven work: explicit boundaries, reusable workflows, and evidence-backed handoffs.

[English](README.md) · [简体中文](README.zh-CN.md) · [Quick start](#quick-start) · [Core principles](docs/CORE-PRINCIPLES.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](pyproject.toml)

## What is Prax?

Prax helps you move from asking an agent to do something to running a workflow with a defined scope, a stopping condition, and a result you can inspect.

Use its native agent from the terminal, bring its workflows into Claude Code or Codex, or delegate a bounded repository task to a coding worker. Skills, tools, memory components, and content workflows extend the product beyond code repair.

**Prax Shift is the continuous-work mode—not the whole product.** Keeping an agent busy is not the goal. Knowing what it may do, when it must stop, and what actually happened is.

## What you can do

| Need | Prax capability | What you get |
|---|---|---|
| Work interactively | Native CLI / REPL with model providers, tools, and sessions | Code analysis, edits, commands, and reusable conversation context |
| Hand off a coding task | Repository harness and Prax Shift with Claude or Codex workers | A frozen task, bounded repair attempts, independent acceptance, and a saved handoff |
| Test without repairing | One-shot `verification` with a repository bridge | One test invocation, per-case accounting, and validated report artifacts; no coding worker |
| Reuse team workflows | Skills, custom agents, MCP tools, and host integration assets | Project-specific procedures and service connections you configure |
| Produce knowledge and content | Research, knowledge-compilation, article, and engineering recipes | Drafts, source records, review checklists, and quality reports |
| Continue work later | Persistent task state, explicit scheduling, and notifications | Bounded continuation of a known task—not an unlimited autonomous backlog |

These are distinct execution paths. A native prompt or a skill does **not** automatically gain the repository harness's contracts and guards.

## The idea behind it

> **Prax = Environment Runtime + Adaptive Autonomy Controller + Evidence Plane**

| Layer | Question | Current repository-task implementation |
|---|---|---|
| **Environment Runtime** | Where does the agent work? | Repository identity, task and policy snapshots, adapter hooks, cooperative locks, and process supervision |
| **Adaptive Autonomy Controller** | When can it continue, and when must it stop? | Explicit scope, cumulative budgets, failure classification, pause, and guarded resume |
| **Evidence Plane** | What supports the result? | Independent verifier output, structured reports, artifact digests, journals, and saved results |

**HITL at decisions. AFK in execution. Evidence at every boundary.**

Humans define intent, authority, and acceptance. Agents execute within those limits. Blockers return to a human instead of silently expanding the task. Today this is rule-based control, not a learned risk policy or a universal approval system. The formula is an architectural direction, not three separately deployable products. See [core principles](docs/CORE-PRINCIPLES.md).

## Quick start

This README describes the source on `main`. A published package can lag behind it; use a source checkout when trying the capabilities documented here.

### Install the runtime

Use Python 3.10+ on macOS or Linux. Node.js is not required for this Python installation; a chosen coding host or project test tool may require its own dependencies.

```bash
git clone https://github.com/ChanningLua/prax-agent.git
cd prax-agent
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/prax --version
.venv/bin/prax shift --help
```

The last two commands check the CLI without a model request, test run, worker, or scheduler. They do not prove a project is ready for execution.

### Choose your entry point

- **Use Prax as an agent:** configure a model endpoint and credentials, then use `prax prompt` or `prax repl`. See [native setup](docs/getting-started.md#native-runtime).
- **Use it inside Codex:** install into the target repository's `.prax/venv`, then run the project-scoped installer and doctor. See [project setup](docs/getting-started.md#codex-project-setup).
- **Use Claude as a worker:** select `worker: "claude"` in the repository profile. Host skill installation is optional for CLI task execution. See [repository harness](docs/repository-harness.md).
- **Connect an existing E2E system:** implement the project evidence bridge described in [one-shot verification](docs/verification-tasks.md).

Do not replace an existing project profile, task, environment, or adapter with a sample. Start with a small task and an acceptance check that already works locally.

### Codex project integration

With Prax already installed in the **target repository's** `.prax/venv`, run from that repository:

```bash
.prax/venv/bin/prax install --target codex --local --dry-run --json
.prax/venv/bin/prax install --target codex --local --json
.prax/venv/bin/prax doctor --target codex --json
```

Prax manages `.codex/skills/prax-shift/SKILL.md` and `.prax/codex-install.json`. It does not write `.agents/skills`, user plugin configuration, or marketplaces. It checks native skill discovery instead of treating copied files as success.

The current isolated discovery probe is **macOS-only** and depends on the installed Codex CLI. Missing support fails closed; it does not fall back to a global plugin. A successful doctor does not prove that an already-open task loaded the skill. Start a new project task and select its project-scoped `prax-shift` skill.

The [official Codex skill directory](https://learn.chatgpt.com/docs/build-skills) is `.agents/skills`; Prax currently uses a separately probed `.codex/skills` compatibility path. This is not a guarantee for every host version. Details and maintenance limits are in [project installation and diagnosis](docs/codex-project-install.md).

### Claude Code integration

Prax also ships Claude skills, commands, agents, rules, hooks, and MCP configuration. The existing `prax install --target claude` installer is **user-scoped** and can change `~/.claude`; it is not equivalent to Codex `--local`. Review [setup and side effects](docs/getting-started.md#claude-integration) before using it.

## Hand off work with Prax Shift

A handoff can start in ordinary language:

> Use Prax Shift to fix the empty-input parser bug. Limit changes to the parser, keep the tests frozen, and use the existing regression test as acceptance. Stop after three worker attempts or ten minutes. Do not commit or publish. Show me the plan first.

The skill helps turn that request into two reviewable files:

- `.prax/harness.json`: worker, policy files, and optional repository adapter.
- `.prax/task.json`: task ID, goal, actual repositories and branches, editable scope, verifier, and total budget.

Once those files describe the real project, use the checked project environment below. If you only installed the source runtime, substitute its absolute CLI path; do not use an unrelated global `prax`.

```bash
.prax/venv/bin/prax shift plan --task .prax/task.json
```

`plan` validates the contract and source identity. It starts **neither tests nor a worker**. After approving that specific coding task, use these commands as needed—not as one sequence:

```bash
.prax/venv/bin/prax shift run --task .prax/task.json
.prax/venv/bin/prax shift status --run-id <task-id>
.prax/venv/bin/prax shift pause --run-id <task-id>
.prax/venv/bin/prax shift resume --run-id <task-id>
```

For `code-check`, acceptance runs first. If it already passes, no worker is needed. Otherwise, a feature failure can drive a bounded repair loop; an environment or identity failure stops it. Resuming requires matching guarded state and keeps the original cumulative budget. Pause is not rollback.

Results live under `.prax/harness-runs/<task-id>/`. A `PASS` with `verified=true` supports the declared acceptance—not deployment, complete business coverage, or every platform. Reading `status` does not re-run acceptance.

Scheduling is optional and explicit. Scheduled Shift continues the same task; it does not invent tomorrow's work, refill its budget, or clear an operator pause. See the [closed-loop guide](docs/recipes/shift-closed-loop.md).

## Run tests without changing code

Use `kind: "verification"` when the request is “run these tests once,” not “keep repairing until green.”

**Prax owns the run and the evidence contract. Your test system owns test execution.**

The repository bridge selects the existing public entry point, enforces current platform authorization, and validates the test system's own evidence. Prax checks the report's run identity, exit/status agreement, and artifact paths and hashes.

- Report `passed`, `failed`, `blocked`, and `not-executed` separately.
- Preserve a concrete blocker instead of turning missing execution into a test failure.
- Keep coverage `unknown`; current execution accounting is not a complete feature-coverage inventory.
- Stop after one invocation: no coding worker, automatic retry, resume, or recurring verification job.

A zero exit code or a JSON-shaped stdout message is not enough. Integration is project-specific; installing Prax alone does not connect an arbitrary E2E system. See the [verification contract](docs/verification-tasks.md).

## Extend the workflows

Prax retains a native agent path alongside its repository control layer:

- **Skills and agents:** bundled and project-local instructions for reusable procedures, with skill discovery and on-demand content access. These guide behavior, not enforce a security boundary.
- **Tools and MCP:** connect local tools and configured services. Credentials and write authority remain explicit integration concerns.
- **Models and sessions:** configurable providers, model routing, and saved conversation state. Native API configuration is separate from Claude/Codex worker authentication.
- **Memory components:** local and SQLite backends, correction records, and experience storage exist; wiring varies by entry point. Do not assume every prompt uses every backend or that a stored inference is verified knowledge.
- **Content workflows:** [article batches](docs/recipes/article-batch.md), [source-grounded drafts](docs/recipes/hotspot-article.md), [release notes](docs/recipes/release-notes.md), [documentation audits](docs/recipes/docs-audit.md), and [support digests](docs/recipes/support-digest.md). External tools are optional dependencies, and quality checks do not independently prove every fact or authorize publication.

## Boundaries worth knowing

- **Prax is not an OS sandbox.** Repository guards detect drift at boundaries; they cannot prevent every side effect or roll changes back. Tests and adapters are trusted executable inputs.
- **Host permissions differ.** Codex workers default to `workspace-write`; Claude workers use `dontAsk`. Those are not equivalent isolation mechanisms.
- **Verification is only as strong as its verifier.** Recognized empty/skipped test runs are rejected, but arbitrary scripts still require trustworthy acceptance logic.
- **Durable state is not automatic recovery from everything.** Hard interruption, detached processes, source drift, or lost ownership can require inspection and a new authorized task.
- **Long-running does not mean unlimited.** No built-in promise of autonomous daily task creation, universal approval integration, automatic production release, or a global kill switch.
- **History is not a current benchmark.** The [benchmark notes](docs/BENCHMARKS.md) describe a narrow historical experiment, not a universal ranking or an end-to-end validation of this version.

Legacy `shift "goal" --verify ...` and `--features` interfaces remain compatibility paths. They do not inherit all guarantees of `shift run --task`.

## Documentation

| Start here | Go deeper |
|---|---|
| [Getting started](docs/getting-started.md) | Runtime installation, native model configuration, and host setup |
| [Core principles](docs/CORE-PRINCIPLES.md) | Product direction, HITL/AFK, and implemented boundaries |
| [Repository harness](docs/repository-harness.md) | Contracts, adapters, checkpoints, result semantics, and trust model |
| [Prax Shift](docs/prax-shift-spec.md) | Shared runtime, continuation, and scheduling limits |
| [One-shot verification](docs/verification-tasks.md) | Existing-test-system integration and evidence protocol |
| [Codex project integration](docs/codex-project-install.md) | Scope, discovery, file protection, and diagnostics |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Prefer a small reproducible case, a focused test, and a clear statement of what the result does—and does not—prove. Improvements to host validation, portable isolation, memory integration, and repeatable evaluations are welcome.

[MIT License](LICENSE).
