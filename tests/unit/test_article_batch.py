from __future__ import annotations

import json

from prax.article_batch import main, plan_batch


def _candidate(index: int, track: str) -> dict:
    return {
        "id": f"topic-{index:02d}",
        "title": f"Business article {index}",
        "track": track,
        "signals": {
            "problem_frequency": 4.5,
            "decision_urgency": 4.5,
            "scenario_specificity": 5,
            "evidence_strength": 4,
            "audience_fit": 5,
            "attention_velocity": 3,
            "cross_source": 4,
            "depth": 5,
            "content_gap": 4,
            "risk": 1,
            "saturation": 2,
        },
        "business_case": {
            "actor": "technical owner",
            "job_to_be_done": "make a production decision",
            "trigger": "a live project",
            "current_workaround": "manual comparison",
            "decision": "build, buy, defer, or stop",
            "cost_of_inaction": "cost and delivery risk",
            "evidence": ["customer brief", "issues"],
        },
        "business_bridge": {
            "source_signal": "A verified event or repeated customer problem.",
            "technical_delta": "A capability or constraint changed.",
            "affected_workflow": "Design, implementation, and acceptance.",
            "business_decision": "Build, buy, defer, or stop.",
            "validation_method": "Run a fixed comparison.",
            "success_metric": "Meet agreed quality, latency, and cost thresholds.",
            "no_fit_conditions": "The workflow does not need this capability.",
        },
    }


def _ten_candidate_payload() -> dict:
    tracks = ["event"] * 3 + ["demand"] * 5 + ["evergreen"] * 2
    return {
        "target_mix": {"event": 0.3, "demand": 0.5, "evergreen": 0.2},
        "portfolio_counts": {"event": 0, "demand": 0, "evergreen": 0},
        "candidates": [
            _candidate(index, track) for index, track in enumerate(tracks, start=1)
        ],
    }


def test_plan_batch_creates_ten_isolated_portfolio_balanced_workspaces(tmp_path):
    manifest = plan_batch(
        _ten_candidate_payload(),
        count=10,
        batch_date="2026-07-29",
        output_root=tmp_path,
    )

    assert manifest.complete
    assert manifest.planned_count == 10
    assert manifest.track_counts == {"event": 3, "demand": 5, "evergreen": 2}
    assert manifest.calibration_gate["status"] == "pending"
    assert manifest.calibration_gate["remaining_slots_released"] is False
    assert (tmp_path / "calibration-gate.json").exists()
    assert len({slot.workspace for slot in manifest.slots}) == 10
    for slot in manifest.slots:
        workspace = tmp_path / f"{slot.position:02d}-{slot.topic_id}"
        assert (workspace / "brief.md").exists()
        assert (workspace / "business-bridge.json").exists()
        assert (workspace / "research-plan.json").exists()
        status = json.loads((workspace / "status.json").read_text(encoding="utf-8"))
        assert status["publish_ready"] is False
        expected_stage = (
            "calibration-pilot"
            if slot.position == 1
            else "waiting-for-calibration"
        )
        assert status["stage"] == expected_stage


def test_cli_returns_one_when_not_enough_longform_candidates(tmp_path):
    source = tmp_path / "topics.json"
    output = tmp_path / "run"
    payload = _ten_candidate_payload()
    payload["candidates"] = payload["candidates"][:2]
    source.write_text(json.dumps(payload), encoding="utf-8")

    exit_code = main(
        [
            str(source),
            "--count",
            "10",
            "--date",
            "2026-07-29",
            "--out-root",
            str(output),
        ]
    )

    assert exit_code == 1
    manifest = json.loads(
        (output / "batch-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["complete"] is False
    assert manifest["planned_count"] == 2


def test_cli_refuses_to_overwrite_existing_batch_without_resume(tmp_path):
    source = tmp_path / "topics.json"
    output = tmp_path / "run"
    output.mkdir()
    (output / "batch-manifest.json").write_text("{}", encoding="utf-8")
    source.write_text(json.dumps(_ten_candidate_payload()), encoding="utf-8")

    exit_code = main([str(source), "--out-root", str(output)])

    assert exit_code == 2
