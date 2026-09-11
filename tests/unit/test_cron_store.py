"""Tests for CronStore — CRUD over .prax/cron.yaml."""

from __future__ import annotations

import pytest

from prax.core.cron_store import (
    CronJob,
    CronStore,
    DuplicateJobError,
    UnknownJobError,
)


@pytest.fixture
def store(tmp_path):
    return CronStore(str(tmp_path))


def _basic_job(**overrides) -> CronJob:
    defaults = dict(name="demo", schedule="*/5 * * * *", prompt="say hi")
    defaults.update(overrides)
    return CronJob(**defaults)


# ── Empty / missing file ─────────────────────────────────────────────────────


def test_load_returns_empty_when_file_missing(store):
    assert store.load() == []


def test_load_returns_empty_when_file_empty(tmp_path):
    (tmp_path / ".prax").mkdir()
    (tmp_path / ".prax" / "cron.yaml").write_text("")
    assert CronStore(str(tmp_path)).load() == []


# ── Save / load round-trip ───────────────────────────────────────────────────


def test_save_and_load_round_trip(store):
    jobs = [
        _basic_job(name="a", schedule="0 9 * * *", prompt="nine"),
        _basic_job(
            name="b",
            schedule="30 17 * * 1-5",
            prompt="five thirty",
            session_id="cron-b",
            model="claude-sonnet-4-8",
            notify_on=["success", "failure"],
            notify_channel="daily",
        ),
    ]
    store.save(jobs)
    loaded = store.load()
    assert len(loaded) == 2
    assert loaded[0].name == "a"
    assert loaded[1].notify_on == ["success", "failure"]
    assert loaded[1].notify_channel == "daily"


def test_load_invalid_yaml_raises(tmp_path):
    (tmp_path / ".prax").mkdir()
    (tmp_path / ".prax" / "cron.yaml").write_text("not: valid: yaml: here")
    with pytest.raises(ValueError, match="not valid YAML"):
        CronStore(str(tmp_path)).load()


def test_load_jobs_must_be_list(tmp_path):
    (tmp_path / ".prax").mkdir()
    (tmp_path / ".prax" / "cron.yaml").write_text("jobs: not-a-list\n")
    with pytest.raises(ValueError, match="must be a list"):
        CronStore(str(tmp_path)).load()


# ── add / remove / get ───────────────────────────────────────────────────────


def test_add_persists_job(store):
    store.add(_basic_job())
    assert [j.name for j in store.load()] == ["demo"]


def test_add_rejects_duplicate_name(store):
    store.add(_basic_job())
    with pytest.raises(DuplicateJobError):
        store.add(_basic_job())


def test_remove_returns_removed_and_persists(store):
    store.add(_basic_job(name="a"))
    store.add(_basic_job(name="b"))
    removed = store.remove("a")
    assert removed.name == "a"
    assert [j.name for j in store.load()] == ["b"]


def test_remove_unknown_raises(store):
    with pytest.raises(UnknownJobError):
        store.remove("ghost")


def test_unknown_job_error_has_friendly_str():
    err = UnknownJobError("ghost")
    assert str(err) == "cron job 'ghost' not found"


def test_get_returns_job(store):
    store.add(_basic_job(name="only", prompt="hello"))
    fetched = store.get("only")
    assert fetched.prompt == "hello"


def test_get_unknown_raises(store):
    with pytest.raises(UnknownJobError):
        store.get("missing")


# ── Validation ───────────────────────────────────────────────────────────────


def test_add_rejects_bad_schedule(store):
    from prax.core.cron_store import InvalidScheduleError
    with pytest.raises(InvalidScheduleError):
        store.add(_basic_job(schedule="60 * * * *"))


def test_add_rejects_empty_name(store):
    with pytest.raises(ValueError, match="non-empty"):
        store.add(_basic_job(name=""))


def test_add_rejects_non_alnum_name(store):
    with pytest.raises(ValueError, match="alphanumeric"):
        store.add(_basic_job(name="bad name!"))


def test_add_rejects_empty_prompt(store):
    with pytest.raises(ValueError, match="prompt"):
        store.add(_basic_job(prompt=""))


def test_add_rejects_bad_notify_on(store):
    with pytest.raises(ValueError, match="notify_on"):
        store.add(_basic_job(notify_on=["always"]))


def test_orchestrate_contract_round_trips(store):
    store.add(
        _basic_job(
            run_mode="orchestrate",
            task_path=".prax/task.json",
        )
    )
    loaded = store.load()[0]
    assert loaded.run_mode == "orchestrate"
    assert loaded.task_path == ".prax/task.json"


def test_shift_rejects_legacy_unverified_jobs(store):
    with pytest.raises(ValueError, match="require --task"):
        store.add(_basic_job(run_mode="orchestrate"))


def test_saved_legacy_shift_remains_manageable_but_not_executable(store):
    legacy = _basic_job(name="legacy", run_mode="orchestrate", agent_host="claude", model="claude-sonnet-4-8")
    store.save([legacy, _basic_job(name="valid")])
    loaded = store.load()
    assert loaded[0].requires_migration
    assert loaded[0].to_dict() == legacy.to_dict()
    with pytest.raises(ValueError, match="require --task"):
        loaded[0].validate()
    store.add(_basic_job(name="new"))
    store.remove("valid")
    assert store.get("legacy").requires_migration
    removed = store.remove("legacy")
    assert removed.to_dict() == legacy.to_dict()
    assert [job.name for job in store.load()] == ["new"]


def test_from_dict_requires_explicit_management_mode_for_legacy_shift():
    raw = _basic_job(run_mode="orchestrate").to_dict()
    with pytest.raises(ValueError, match="require --task"):
        CronJob.from_dict(raw)
    assert CronJob.from_dict(raw, allow_legacy=True).requires_migration


@pytest.mark.parametrize("task_path", ["", " ", 42, "../other/task.json"])
def test_management_mode_does_not_allow_malformed_new_contracts(store, task_path):
    store.save([_basic_job(run_mode="orchestrate", task_path=task_path)])
    # Empty strings are omitted by the historical serializer and become legacy
    # entries. A present malformed task field must still fail validation.
    if task_path == "":
        store.path.write_text("jobs:\n- name: bad\n  schedule: '* * * * *'\n  prompt: old\n  run_mode: orchestrate\n  task_path: ''\n")
    with pytest.raises(ValueError):
        store.load()


@pytest.mark.parametrize("task_path", ["../other/task.json", "/tmp/task.json", 42])
def test_shift_rejects_unsafe_task_path(store, task_path):
    with pytest.raises(ValueError):
        store.add(_basic_job(run_mode="orchestrate", task_path=task_path))


def test_shift_cannot_override_frozen_worker(store):
    with pytest.raises(ValueError, match="harness.json"):
        store.add(_basic_job(run_mode="orchestrate", task_path=".prax/task.json", agent_host="claude"))


def test_agent_host_requires_orchestrate_mode(store):
    with pytest.raises(ValueError, match="only valid"):
        store.add(_basic_job(agent_host="codex"))


def test_rejects_unknown_agent_host(store):
    with pytest.raises(ValueError, match="agent_host"):
        store.add(_basic_job(run_mode="orchestrate", agent_host="other"))


def test_from_dict_missing_field():
    with pytest.raises(ValueError, match="missing required field"):
        CronJob.from_dict({"name": "x", "schedule": "* * * * *"})  # no prompt
