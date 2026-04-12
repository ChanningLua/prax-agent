# Benchmarks

![Framework comparison](./assets/benchmark-frameworks.svg)

## Summary

Prax was evaluated against three peer projects on the same Claude-family repository-repair task family:

- `Hermes`
- `HyperAgents`
- `oh-my-openagent`

The shared benchmark used:

- 10 repeated rounds per framework
- one repository-fix case per round
- preserved session state between rounds for each framework
- the same success criterion: the workspace test suite must pass

Cross-framework baseline results:

| Framework | Solved | Timed Out | Avg Seconds |
|---|---:|---:|---:|
| Prax | 8/10 | 2 | 58.44 |
| Hermes | 8/10 | 2 | 56.99 |
| HyperAgents | 1/10 | 1 | 58.72 |
| oh-my-openagent | 8/10 | 2 | 55.32 |

## Relative Read

On this repository-repair suite:

- **Prax vs Hermes**: they started from the same `8/10` baseline tier, but Prax benefited more from harness-level verification fixes.
- **Prax vs HyperAgents**: Prax was much more stable on repeated repo-fix loops, while HyperAgents looked more like a research prototype than a benchmark-stable harness here.
- **Prax vs oh-my-openagent**: baseline solve rate was similar, but Prax kept the clearer native product story around orchestration, memory, and middleware.

## Prax Improvement Story

![Prax improvement](./assets/benchmark-prax-improvement.svg)

Prax's initial failures were not caused by weak code editing. They were caused by verification friction:

- the model tried to run `pytest -q`
- `SandboxBash` classified the action as dangerous under `workspace-write`
- the harness burned iterations on delegation and model escalation without gaining fresh verification signal

We fixed that by making verification a first-class path:

1. `VerifyCommand` for bounded repo-local verification
2. automatic downgrade of safe verification commands inside `SandboxBash`
3. verification guidance middleware to force repair-or-rerun behavior
4. no model upgrade once verification has already passed

After those changes, Prax-only reruns reached:

- `10/10` solved
- `0` timeouts
- `29.56s` average

## Method

Each round creates a fresh tiny repository with:

- one intentionally broken function in `src/`
- one focused regression test in `tests/`

The agent receives a task of the form:

`Run pytest -q, fix the bug in src/<module>.py so the failing test passes, do not change tests, and stop after the fix is complete.`

The repeated-round design is intentional. Some frameworks claim to improve over repeated use, so the benchmark preserves their session/history state across rounds.

## What This Benchmark Measures Well

- repository repair from failing tests
- verification-loop quality
- time-to-green
- repeated-session stability
- whether the harness spends time productively or burns turns on orchestration mistakes

## What It Does Not Measure

- open-domain research quality
- multi-hour planning workflows
- front-end / multimodal tasks
- MCP-heavy knowledge retrieval
- interactive TUI excellence

This benchmark is therefore a repository-repair benchmark, not a universal agent intelligence score.

## Reproducibility

This benchmark was run on 2026-04-13 with four local framework checkouts and an external driver that emitted `results.json` and `summary.md` artifacts per run.

Today, the benchmark evidence in this repo is the documented result summary, not a fully vendored benchmark harness yet. That is deliberate and temporary: moving repeated evaluation fully in-repo is one of the next productization steps for Prax.

If you want to reproduce the run shape, keep these constraints the same:

- 10 repeated rounds per framework
- one fresh repository-fix case per round
- preserved session state inside each framework across rounds
- identical success criterion: the workspace test suite must pass

## Takeaway

Prax's core value is not that it has the biggest feature list.

Its value is that:

- multi-model orchestration is explicit
- memory and middleware are first-class
- the verification loop is now benchmark-proven
- benchmark-driven changes materially improved the harness without changing its overall architecture
