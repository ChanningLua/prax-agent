"""Unit tests for prax/core/run_journal.py."""
from __future__ import annotations

import json

from prax.core.run_journal import RunJournal, RUN_JOURNAL_SCHEMA_VERSION


class TestRunJournal:
    def test_record_and_events_roundtrip(self, tmp_path):
        j = RunJournal(str(tmp_path), "run_001")
        j.record("step.plan", output="planned", type="step", input={"goal": "x"})
        j.record("step.code", output="coded")

        events = j.events()
        assert [e["seq"] for e in events] == [0, 1]
        assert [e["step"] for e in events] == ["step.plan", "step.code"]
        assert events[0]["output"] == "planned"
        assert events[0]["input"] == {"goal": "x"}
        assert events[0]["type"] == "step"
        assert events[0]["ts"]
        assert events[0]["v"] == RUN_JOURNAL_SCHEMA_VERSION

    def test_journal_is_jsonl_on_disk(self, tmp_path):
        j = RunJournal(str(tmp_path), "run_002")
        j.record("a", output=1)
        j.record("b", output=2)

        path = tmp_path / ".prax" / "runs" / "run_002" / "journal.jsonl"
        assert path.exists()
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["step"] == "a"
        assert json.loads(lines[1])["step"] == "b"

    def test_result_for_returns_last_output(self, tmp_path):
        j = RunJournal(str(tmp_path), "run_003")
        j.record("verify", output="fail")
        j.record("verify", output="pass")
        assert j.result_for("verify") == "pass"
        assert j.result_for("never") is None

    def test_done_steps(self, tmp_path):
        j = RunJournal(str(tmp_path), "run_004")
        j.record("plan")
        j.record("code")
        assert j.done_steps() == {"plan", "code"}

    def test_resume_across_instances(self, tmp_path):
        j1 = RunJournal(str(tmp_path), "run_005")
        j1.record("plan", output="done")

        # fresh process / next cron window: new instance, same run_id
        j2 = RunJournal(str(tmp_path), "run_005")
        assert [e["step"] for e in j2.events()] == ["plan"]
        assert j2.result_for("plan") == "done"

        # appends continue the seq, they don't restart
        j2.record("code", output="done2")
        assert [e["seq"] for e in j2.events()] == [0, 1]

    def test_tolerates_corrupt_trailing_line_and_continues(self, tmp_path):
        j = RunJournal(str(tmp_path), "run_006")
        j.record("good", output=1)

        # simulate a crash mid-write: a truncated final line with no newline
        path = tmp_path / ".prax" / "runs" / "run_006" / "journal.jsonl"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write('{"seq": 1, "step": "half')

        # read tolerance: the corrupt tail is skipped on replay
        assert [e["step"] for e in j.events()] == ["good"]

        # write robustness: next append starts on a fresh line and gets seq 1
        j.record("after", output=2)
        events = j.events()
        assert [e["step"] for e in events] == ["good", "after"]
        assert [e["seq"] for e in events] == [0, 1]

    def test_empty_journal(self, tmp_path):
        j = RunJournal(str(tmp_path), "run_007")
        assert j.events() == []
        assert j.result_for("x") is None
        assert j.done_steps() == set()
