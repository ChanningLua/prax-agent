# One-shot verification tasks

Prax can supervise a reviewed project's existing test system without making a
coding worker repair failures. It does not provide a second E2E framework.

| Task kind | On acceptance failure | Worker | Repeat / resume |
| --- | --- | --- | --- |
| `code-check` | Bounded repair loop | Codex or Claude | Guarded continuation |
| `verification` | Report and stop | None | Not supported |

Use `verification` when the requested outcome is a test run, not a code change.
Setting a coding task's `max_iterations` to 1 does not make it test-only.

## Contract and current authorization

The repository must have a reviewed lifecycle adapter and a verifier bridge
that emits `prax-verification-v1` evidence. Platform-specific knowledge,
environment selection, artifact checks and test execution remain in the project.
Missing bridge support is a blocker, not permission to invoke a raw device runner.

The assistant prepares a unique task using existing project paths and the actual
current branches. This example is a shape, not a ready-to-run project command:

```json
{
  "schemaVersion": 1,
  "id": "authorized-verification-attempt",
  "goal": "Run the explicitly selected test cases once, without changing source",
  "kind": "verification",
  "repositories": [{"path": ".", "branch": "<actual-current-branch>", "scope": []}],
  "verifier": {
    "cwd": ".",
    "argv": ["python3", "tools/verification_bridge.py", "--request", ".prax/request.json"],
    "timeout_seconds": 120,
    "report_schema": "prax-verification-v1",
    "control_files": ["tools/verification_bridge.py", ".prax/request.json"]
  },
  "budget": {"wall_seconds": 150}
}
```

`scope` must be empty: no source edit is authorized. The worker iteration and
stuck limits normalize to zero. Freeze all project-required request, issue,
plan, artifact-manifest and bridge control files before execution. A plan is
read-only and neither runs the adapter nor proves the target tests are runnable.

```bash
prax shift plan --task .prax/task.json
# Only after the user authorizes the actual test execution:
prax shift run --task .prax/task.json --authorize-verification
prax shift status --run-id authorized-verification-attempt
```

The execution flag is an operator declaration for this invocation, not a
permission recovered from a saved task or proof of user identity. Project
adapters must independently require the current precise platform, case set,
environment and side-effect authorization. Do not derive permission from device
availability, earlier runs or an instruction to prepare the integration.

The runtime writes a durable dispatch marker before invoking the verifier.
It never starts a coding worker. Failure, timeout or an interrupted dispatch
cannot be resumed or automatically repeated, including through
`--resume-existing`. A new attempt needs explicit new authorization and a new
task; do not silently rotate IDs, delete the marker, or create a recurring job.

## Bridge protocol

The supervisor supplies these values only for the authorized one-shot process:

- `PRAX_RUN_ID`: frozen task ID.
- `PRAX_EXECUTION_ID`: fresh execution identity.
- `PRAX_VERIFICATION_DIR`: fresh output directory within the run evidence tree.
- `PRAX_VERIFICATION_REPORT`: report file within that directory.
- `PRAX_VERIFICATION_AUTHORIZED=1`: current invocation opted into execution.

The bridge invokes its existing public test entry once, preserves raw output,
validates that system's authoritative evidence, and atomically writes the report.
It must not edit code, bypass project platform checks, automatically build/install
artifacts, retry a failure or resume an older test-system attempt.

```json
{
  "schemaVersion": 1,
  "run_id": "<PRAX_RUN_ID>",
  "execution_id": "<PRAX_EXECUTION_ID>",
  "status": "passed",
  "tests": [{"id": "declared-case-id", "status": "passed"}],
  "coverage": {"status": "unknown", "reason": "No complete feature-to-test map was supplied"},
  "evidence": [{"path": "<absolute-file-within-PRAX_VERIFICATION_DIR>", "sha256": "<SHA-256>"}],
  "context": {"platform": "<explicit-platform>", "environment": "<explicit-environment>"}
}
```

Tests have unique IDs and one of `passed`, `failed`, `blocked`, `not-executed`.
The summary counts these states separately; `completed` means passed plus failed,
not a guess about whether a blocked test performed partial business actions.
Passing requires a nonempty all-passed set and valid evidence; failed requires
at least one failed test and no blocked test. Blocked reports may have no test
execution. Exit codes are 0/1/2 for passed/failed/blocked and must agree with the
report. Missing, stale, malformed or inconsistent evidence is not a pass.

Prax checks report identity and referenced file paths/digests. The project bridge
remains responsible for validating its test system's schema, sealed bundle,
complete requested case set, acceptance assertions, source/artifact identity and
platform authorization. A stdout message or process exit code alone is insufficient.
For example, a project with a proof kernel should reuse that kernel's evidence
validator rather than reimplementing it in Prax.

Execution accounting and coverage are different dimensions. A failed test was
still tested; a planned test skipped after a blocker was not executed. Without
a feature-to-test inventory, coverage stays `unknown`, not zero or complete.

## Cancellation and limits

The normal hard execution budget and a separate, at-most-12-second verification
cleanup allowance apply. Preserve the original failure/timeout and any inability to
confirm cleanup. Do not claim a whole platform passed, all processes stopped,
or business coverage was complete without the corresponding evidence.

The runtime owns its process group; a project test system that creates detached
groups must own cancellation of those groups. Hard process death can prevent
normal test-system finalization. Such an attempt remains interrupted/unverified
and is not automatically retried. These cooperative source and process guards
are not a sandbox against hostile tests or an external writer.

This mode reports validation only. Build/install, source repair, publishing,
deployment and scheduled work are separate authorizations.
