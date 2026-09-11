---
name: prax-shift
description: Hand coding work or explicitly authorized one-shot verification to Prax Shift, inspect a shift, or schedule supported coding work with Claude as the worker. Use for Prax handoff, unattended coding, delegated test execution through a project adapter, run evidence, or requests to report, pause, resume, or stop ongoing Prax work.
---

# Prax Shift for Claude

Use Shift as one Prax operating mode. Prax owns durable state, verification,
approval boundaries, scheduling, and evidence; Claude is a replaceable worker.

## Map the user's intent

- For start or handoff, require a bounded goal, verifier, workspace, and budget.
- For test-only handoff, use the one-shot verification path below; planning or
  integration work alone does not authorize test execution.
- For status, use `prax shift status --run-id <id>` to inspect saved evidence
  in `.prax/harness-runs/<id>/`, without starting work or claiming fresh verification.
- For continuing coding work, resume the frozen contract with the same run ID.
  One-shot verification cannot resume.
- For pause or stop, persist a pause request and preserve evidence. Remove a
  schedule only when explicitly requested.
- Create a recurring schedule only when explicitly requested.

## Operating contract

- Keep intent, irreversible decisions, production actions, and scope changes
  HITL.
- Execute AFK only inside the requested workspace and budget.
- Require an independent verifier for coding work. Without one, report the
  missing acceptance criterion and do not start a coding worker.
- Keep Claude at `dontAsk`: operations that still require approval are denied.
  This is not an OS sandbox. Do not bypass permissions to keep AFK work going;
  missing narrowly preauthorized capabilities require a human decision.
- Never auto-push, auto-merge, deploy, spend money, or widen permissions.
- Treat `HARNESS-ERROR` as an environment failure, not a code defect.

## Coding: prepare and run

Prepare the files for the user after inspecting existing configuration. Do not
overwrite another task or unrelated work. In `.prax/harness.json`, use
`schemaVersion: 1`, `worker: "claude"`, and relevant existing `policy_files`
(or `[]`); optional `model` is frozen too. Preserve an existing worker choice
unless the user asks to change it.

Create `.prax/task.json` with `schemaVersion: 1`, a unique `id`, the agreed
`goal`, and `kind: "code-check"`. Declare `repositories` with workspace-relative
`path`, the actual current `branch`, and narrow relative `scope` arrays.
Declare `verifier` with trusted `argv`, workspace-relative `cwd`, and
`timeout_seconds`; use explicit pytest test paths. Optional `control_files`
binds additional existing scripts/fixtures using workspace-relative paths.
Keep acceptance outside editable scope. Set `budget` with `max_iterations`,
`wall_seconds`, `stuck_after`; default conservatively to `3`, `600`, `2` when
unspecified and disclose these limits.

Use `prax` or the active environment's `python -m prax`. Plan validates without
starting tests or a worker:

```bash
prax shift plan --task .prax/task.json
prax shift run --task .prax/task.json
```

Continue it:

```bash
prax shift status --run-id <id>
prax shift pause --run-id <id>
prax shift resume --run-id <id>
```

Pause requests supervisor shutdown; inspect status to confirm it stopped.
Resume checks frozen controls and source identity and retains the total budget.
Do not delete evidence, change acceptance, or rotate task IDs to bypass a
pause, identity mismatch, or exhausted budget. Escalate for a new task if needed.

Schedule a recurring Shift:

```bash
prax cron add \
  --name "<job-name>" \
  --schedule "<five-field cron>" \
  --run-mode shift \
  --task .prax/task.json
prax cron install
```

The scheduler replays `prax shift run --task .prax/task.json --resume-existing`;
it cannot unpause or renew a budget. Do not create a schedule unless explicitly
requested. Follow host scheduling instructions and avoid duplicate schedulers.
Legacy goal/feature commands do not provide this guarded task contract.

## One-shot verification

For existing tests without code repair, use `kind: "verification"`, empty
repository `scope` arrays, a `wall_seconds` budget, and
`verifier.report_schema: "prax-verification-v1"`. Require a reviewed project
adapter and read its documentation for argv, authorization and evidence. Do not
invent another test framework or bypass the existing public E2E entry point.

Validate with `prax shift plan --task <task.json>`. Only after current explicit
authorization run `prax shift run --task <task.json> --authorize-verification`.
Obtain the project's exact platform, case or explicit case set, environment,
artifacts and side-effect authorization before E2E; never reuse old permissions.

No coding worker is constructed. Failure or interruption does not permit a retry,
resume, `--resume-existing`, recurring schedule, or automatic task-ID rotation.
Another attempt needs fresh authorization. Use structured results and sealed
evidence to distinguish passed, failed, blocked and not-executed tests; a process
exit code alone proves nothing, and missing coverage mapping means unknown.

Always hand back the run ID, stop reason, verifier verdict, changed files,
waiting-on-human decisions, and an exact continuation command when supported.
For verification, hand back status/evidence and any new authorization needed,
never a resume command.

For an integration-only check, do not modify files. Return
`PRAX_CLAUDE_SHIFT_OK` with the resolved Prax executable path.
