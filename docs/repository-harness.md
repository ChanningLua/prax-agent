# Repository harness

Prax provides the reusable execution and evidence layer. A repository supplies
its own policy, scope, acceptance command, and optional lifecycle adapter.
No project names, branch conventions, device runners, or private service URLs
belong in the runtime. `prax shift run --task` uses this same runtime;
legacy goal/feature Shift interfaces remain separate compatibility paths.

The harness supports explicit `code-check` tasks and one-shot `verification`
tasks on macOS/Linux. See [one-shot verification](verification-tasks.md) for
the no-worker execution path and structured project evidence protocol.
For `code-check`, it uses Codex at `workspace-write` or Claude at `dontAsk`; tests can inject any
executor implementing `run(instruction, session_id=...) -> StepResult`.
Coding workers are replaceable without moving task state or acceptance into the model.
`dontAsk` denies operations that still need approval; it is not a filesystem
sandbox. Missing permissions must be resolved by the operator, not silently
bypassed. Both step adapters start fresh model contexts. Optional `model` is
frozen in the profile; Claude requires an explicit `claude-` model ID.

## A portable coding task

Create `.prax/harness.json` in your repository:

```json
{
  "schemaVersion": 1,
  "worker": "codex",
  "policy_files": ["AGENTS.md"]
}
```

Create `.prax/task.json`, choosing an **existing real branch**, concrete paths,
and an acceptance test that independently proves this particular goal:

```json
{
  "schemaVersion": 1,
  "id": "parser-empty-input",
  "goal": "Return an empty result for empty parser input.",
  "kind": "code-check",
  "repositories": [{"path": ".", "branch": "main", "scope": ["src/parser.py"]}],
  "verifier": {
    "cwd": ".",
    "argv": ["python3", "-m", "pytest", "tests/test_parser.py", "-q"],
    "timeout_seconds": 60
  },
  "budget": {"max_iterations": 3, "wall_seconds": 600, "stuck_after": 2}
}
```

```bash
prax harness plan --task .prax/task.json
prax harness run --task .prax/task.json
prax harness status --run-id parser-empty-input
prax harness pause --run-id parser-empty-input
prax harness resume --run-id parser-empty-input
```

`plan` reads and validates source identity without starting a worker or test.
`run` freezes the task and profile; an existing ID requires `resume` or
`run --task ... --resume-existing`. Only explicit `resume` can clear a pause.
`status` only reads the saved result. `resume` requires the same policy,
acceptance files, branch, HEAD, index, and last guarded working-tree state.
It cannot change the goal or silently replenish the total budget.

Use explicit pytest test paths. Selected Python test files/directories (up to
200 files) and nearby pytest configuration are fingerprinted; unittest
discovery also has a bounded selection fingerprint. Additional verifier
fixtures/helpers can be frozen with `verifier.control_files`, an optional
array of up to 200 workspace-relative existing files. Automatic inspection
does not discover every transitive import or external dependency.

Different task IDs still acquire the same cooperative lock in each declared
Git common directory. This prevents two Prax harness tasks from editing the
same repository concurrently, even when the project has no lifecycle adapter.
An adapter may additionally enforce the project's existing ownership protocol.

Before dispatch, the harness runs acceptance once. An already passing
acceptance requires zero worker calls. An environment failure parks the run.
A feature failure can drive a bounded repair loop, with the failure output
fed back into the next fresh worker context. Worker crashes stop after one
attempt. The verifier runs independently after a worker returns; its claim of
completion has no authority.

Recognized pytest/unittest test invocations that report only skipped tests,
zero tests, or collection/inspection without execution are environment errors,
not acceptance. The same check recognizes standard Node/Vitest skip summaries.
Custom reporters and arbitrary verification scripts retain trusted exit-status
semantics; Prax cannot infer execution counts from every output format. A
successful command proves only the declared tests, not their completeness.

## Project adapter

For repository-specific ownership or command routing, add:

```json
"adapter": {"argv": ["python3", "tools/harness_adapter.py"]}
```

Prax invokes this argv without a shell, appending:

```text
--phase preflight|claim|check|release
--root /resolved/workspace
--run-dir /resolved/workspace/.prax/harness-runs/task-id
--contract /resolved/workspace/.prax/harness-runs/task-id/contract.json
```

`preflight` validates project policy before dispatch. `claim` acquires the
project's cooperative ownership. `check` runs at execution/verification
boundaries. `release` is attempted only when Git/scope guards still hold.
The verifier, `check`, and `release` must leave guarded source and controls
unchanged. Prax compares before/after snapshots; a release failure or mutation
invalidates PASS. A worker error can preserve legal partial edits as a guarded
checkpoint after successful release, but never as verified completion. Resume
verifies that checkpoint first and keeps the original cumulative budget.
Hooks must be bounded, idempotent, noninteractive, and local. They may not
run platform tests or send messages. A nonzero exit or a line beginning with
`HARNESS-ERROR:` stops the run. If a multi-repository claim partially succeeds,
the adapter must retain/report or explicitly unwind its own claims; Prax
does not guess how project ownership works.

## Evidence and exit codes

Each task owns `.prax/harness-runs/<id>/`: frozen contract, guarded identity,
worker journal, full verifier logs, runtime log, and `result.json`.

| Result | CLI exit | Meaning |
|---|---:|---|
| `PASS` | 0 | The declared acceptance passed against the guarded source |
| `FEATURE-FAIL` | 1 | Acceptance failed within the bounded repair window |
| `BLOCKED` | 2 | A one-shot verification reports blocked execution with structured evidence |
| `HARNESS-ERROR` | 2 | Environment, execution, identity, or ownership blocked acceptance |
| `PAUSED` | 3 | Operator pause; scheduler must not resume it |

Status inspection exits 0 when its saved result can be read; it does not
perform fresh verification. Test execution success proves only the declared
acceptance, never deployment, an undeclared platform, or other repository
changes. Code-check `result.json` explicitly marks platform validation and
publication as not executed. Verification results retain the project's exact
test statuses, context and evidence; they do not claim undeclared platforms.

`RUNNING` is transient evidence, never acceptance. Every supervised attempt
has a fresh execution ID; an old result cannot masquerade as the new child's
output. Cached PASS reuse is allowed only after contract/control and source
identity checks; external services or dependencies may still have changed.
Use a new explicitly authorized task for a fresh external-world check.

The supervisor owns one process group and terminates it on timeout,
interruption, and normal exit, including descendants left by a finished
worker parent. TERM/HUP/INT cannot interrupt its final TERM/KILL/wait cleanup.
The runtime inherits repository/run lock descriptors and watches a private
parent-lifetime pipe. If the supervisor dies, including SIGKILL, the runtime
kills its dedicated process group; its inherited locks prevent another Prax
task taking ownership while that runtime is still alive. This does not cover
hostile descendants that deliberately escape the group or rewrite the runtime.
Elapsed time and worker dispatches persist across resumes.
After a hard timeout or source drift, inspect evidence and ownership before
continuing; there is no automatic retry, branch switch, stash, or cleanup of
user changes. An interrupted run may require an explicit handoff and new task.
Pause is checked while the worker runs and terminates its process group. A
guarded partial checkpoint can be resumed, but pause is not a transaction or
rollback. A supervisor killed before final evidence leaves `RUNNING` and is
refused on resume pending inspection, rather than silently renewing budget.

Ignore `.prax/harness-runs/` and `.prax/logs/cron/` in Git. Runtime evidence
directories (`.prax/harness-runs`, `.prax/runs`, `.prax/logs/cron`) are excluded
from source checkpoints; other `.prax` files remain guarded. Share profiles and task templates, not
private run logs. The public examples contain no private project data.

## Trust and present limits

The task, verifier, tests, repository policy, and adapter are trusted operator
inputs. Running argv without a shell is not a sandbox: a test or repository
script can execute arbitrary code. Scope guards detect cooperative drift at
boundaries, after changes may already have occurred; they do not roll it back.
Only declared repositories are inspected, avoiding an implicit scan of every
submodule. In-scope files and tracked changes use content hashes; unrelated
untracked outputs use metadata fingerprints to avoid reading large artifacts
on every step. Policy and resolved acceptance files are fingerprinted so changes
invalidate reuse. This is not a defense against a hostile worker that can
rewrite the harness process or all its evidence.

GUI checks, platform regression, build/install, production actions,
notifications, remote publishing, and recurring schedules remain separate,
explicitly authorized workflows. A project may integrate its existing proof
kernel through its own reviewed adapter using a one-shot `verification` task;
Prax does not provide a second device runner or infer platform authority from old
runs. The `code-check` repair path continues to exclude platform E2E.

Run offline tests with:

```bash
python -m pytest tests/unit/test_harness_contract.py tests/unit/test_repository_harness.py tests/unit/test_harness_parent_lifecycle.py tests/unit/test_harness_acceptance_evidence.py tests/unit/test_harness_checkpoint_safety.py -q
```

These exercise temporary Git fixtures and fake workers, not live agent
accounts or platform devices.
