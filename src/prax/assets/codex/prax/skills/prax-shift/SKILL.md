---
name: prax-shift
description: Hand coding work or explicitly authorized one-shot verification to Prax Shift, inspect a shift, or schedule supported coding work with Codex as the worker. Use for Prax handoff, unattended coding, delegated test execution through a project adapter, run evidence, or requests to report, pause, resume, or stop ongoing Prax work.
---

# Prax Shift for Codex

Use Prax as the control and evidence layer. Treat Shift as one Prax operating
mode, not as the whole product. Codex is a replaceable worker; it does not own
completion criteria, durable state, or the autonomy boundary.

## Map the user's intent

- **Start / hand off:** require a bounded goal, verifier, workspace, and budget;
  then run one Shift window.
- **Test only:** use the one-shot verification path below, not the coding repair
  loop. Preparing an integration or inspecting a plan does not authorize tests.
- **Report / status:** use `prax shift status --run-id <id>` to inspect saved
  `.prax/harness-runs/<id>/` evidence. Do not start work or claim fresh verification.
- **Continue / resume:** for coding tasks, use the frozen contract and explicit
  resume command. One-shot verification cannot resume.
- **Pause / stop:** persist a pause request and preserve evidence. Remove a
  recurring cron job only when the user explicitly asks to cancel its schedule.
- **Schedule:** create recurring execution only when the user explicitly asks.

## Operating contract

- Keep intent, irreversible decisions, production actions, and scope changes
  HITL.
- Execute AFK only inside the requested workspace and budget.
- Require an independent verifier for coding work. Without one, report the
  missing acceptance criterion without starting a coding worker or claiming success.
- Never use Codex `--yolo`. Keep the adapter at `workspace-write`.
- Do not auto-push, auto-merge, deploy, spend money, or widen permissions.
- Treat `HARNESS-ERROR` as an environment failure, not a product defect. Do not
  change tests, permissions, scope, or budget just to make a run pass.

## Coding: run and continue

Use `prax`, or the active environment's `python -m prax`.

Prepare the contract for the user; do not make them hand-write CLI flags. Read
existing files first, preserve unrelated work, and do not overwrite a frozen
task. In `.prax/harness.json`, set `schemaVersion: 1`, `worker: "codex"`, and
`policy_files` to existing, relevant policy paths (or `[]`). An optional `model`
is frozen with the profile. Do not silently replace an existing worker choice.

Create a workspace-local `.prax/task.json` with these fields:

- `schemaVersion: 1`, a unique `id`, the agreed `goal`, `kind: "code-check"`.
- `repositories`: explicit repository roots relative to the workspace; each has
  `path`, its actual current `branch`, and a narrow relative `scope` array.
- `verifier`: trusted `argv` array, workspace-relative `cwd`, and
  `timeout_seconds`. Use explicit pytest test paths; never a shell command
  hidden in the goal. Put extra fixtures/scripts in optional `control_files`
  (workspace-relative existing files) when acceptance depends on them.
- `budget`: `max_iterations`, `wall_seconds`, `stuck_after`; absent a user
  preference, use conservative limits `3`, `600`, `2` and disclose them.

Keep existing acceptance tests out of the editable scope. New goals, changed
acceptance, or more budget require an explicit new task, not an automatic ID
rotation. Validate first; a plan does not execute a verifier or worker.

```bash
prax shift plan --task .prax/task.json
prax shift run --task .prax/task.json
```

Continue from durable evidence:

```bash
prax shift status --run-id <id>
prax shift pause --run-id <id>
prax shift resume --run-id <id>
```

`pause` requests supervisor shutdown; inspect status to confirm it stopped.
Resuming verifies contract and code identity, does not replenish budgets, and
may require human inspection after a hard interruption. Never automatically
delete a pause flag or edit run evidence to get past a blocker.

For an explicitly requested recurring run, use the host's supported scheduling
tool and replay only this command in the fixed workspace:

```bash
prax shift run --task .prax/task.json --resume-existing
```

It cannot unpause or renew the task. Stay quiet on unchanged non-actionable
state. Use `prax cron add --run-mode shift --task .prax/task.json` (plus explicit
name/schedule) only when the user requests Prax-managed scheduling; never
install two schedulers for the same task. Legacy goal/feature Shift commands
remain compatibility interfaces, not substitutes for this guarded task path.

## One-shot verification

When the user asks Prax to run existing tests without fixing code, use
`kind: "verification"`, empty repository `scope` arrays, a `wall_seconds`
budget, and `verifier.report_schema: "prax-verification-v1"`. A reviewed project
adapter must supply the explicit verifier argv and structured evidence protocol.
Read that repository's adapter documentation; do not invent a platform runner or
relax its command allowlist. Missing adapter support is a blocker.

Prepare the contract and run `prax shift plan --task <task.json>` first. Only
with current authorization execute
`prax shift run --task <task.json> --authorize-verification`. For E2E, obtain
the exact platform, case or explicit case set, environment, artifacts and side
effects required by the project. Do not infer these from an earlier run.

This path dispatches no coding worker and cannot resume, automatically retry,
or use `--resume-existing`/recurring scheduling. Even an interrupted dispatch
consumes the attempt. Preserve its evidence and ask for fresh authorization
before another run; never rotate IDs automatically. Read the report's test
statuses and evidence, not only the process exit code. Report coverage as unknown
unless a separate supported feature-to-test mapping establishes it.

## Evidence handoff

Report the resolved worker, run ID, stop reason, verifier and verdict, changed
files or report artifact, waiting-on-human decision, and exact continuation
command when supported. For verification, provide status/evidence and any new
authorization needed instead of a resume command.

For an integration-only check, do not modify files. Return
`PRAX_CODEX_SHIFT_OK` with the resolved Prax executable path.
