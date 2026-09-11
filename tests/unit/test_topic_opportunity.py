from __future__ import annotations

import json
from pathlib import Path

from prax.topic_opportunity import main, rank_opportunities


def _candidate(
    candidate_id: str,
    track: str,
    *,
    attention: float,
    demand: float,
    risk: float = 1,
    complete_case: bool = True,
    complete_bridge: bool = True,
) -> dict:
    business_case = {
        "actor": "engineering manager",
        "job_to_be_done": "make an implementation decision",
        "trigger": "a live customer project",
        "current_workaround": "manual research",
        "decision": "build or buy",
        "cost_of_inaction": "delivery delay",
        "evidence": ["customer brief", "three recurring questions"],
    }
    if not complete_case:
        business_case = {"actor": "someone"}
    business_bridge = {
        "source_signal": "A concrete event or recurring customer signal.",
        "technical_delta": "A capability or system constraint changed.",
        "affected_workflow": "The implementation and approval workflow.",
        "business_decision": "Adopt, defer, build, buy, or combine.",
        "validation_method": "Run a reproducible comparison on a fixed task set.",
        "success_metric": "Meet the agreed quality, latency, and cost thresholds.",
        "no_fit_conditions": "The change does not affect this workflow.",
    }
    if not complete_bridge:
        business_bridge = {
            "source_signal": "A product announcement is receiving attention."
        }
    return {
        "id": candidate_id,
        "title": f"Candidate {candidate_id}",
        "track": track,
        "signals": {
            "attention_velocity": attention,
            "cross_source": attention,
            "problem_frequency": demand,
            "decision_urgency": demand,
            "scenario_specificity": demand,
            "evidence_strength": 4,
            "audience_fit": 5,
            "content_gap": 4,
            "depth": 5,
            "risk": risk,
            "saturation": 2,
        },
        "business_case": business_case,
        "business_bridge": business_bridge,
    }


def test_empty_portfolio_prioritizes_demand_track_over_bigger_news_spike():
    payload = {
        "candidates": [
            _candidate("news", "event", attention=5, demand=4),
            _candidate("customer-problem", "demand", attention=3, demand=4),
        ]
    }

    report = rank_opportunities(payload)

    assert report.selected_id == "customer-problem"
    assert report.selected_track == "demand"
    assert "portfolio deficit" in report.selection_reason


def test_on_target_portfolio_selects_highest_opportunity_score():
    payload = {
        "portfolio_counts": {"event": 3, "demand": 5, "evergreen": 2},
        "candidates": [
            _candidate("news", "event", attention=5, demand=5),
            _candidate("customer-problem", "demand", attention=2, demand=4),
        ],
    }

    report = rank_opportunities(payload)

    assert report.selected_id == "news"
    assert "highest opportunity score" in report.selection_reason


def test_missing_underfilled_track_candidate_falls_back_without_false_deficit_claim():
    payload = {
        "portfolio_counts": {"event": 8, "demand": 1, "evergreen": 1},
        "candidates": [_candidate("only-news", "event", attention=5, demand=5)],
    }

    report = rank_opportunities(payload)

    assert report.selected_id == "only-news"
    assert "no eligible candidate in an underfilled track" in report.selection_reason
    assert "deficit -" not in report.selection_reason


def test_event_without_business_bridge_is_routed_to_digest_not_longform():
    payload = {
        "portfolio_counts": {"event": 0, "demand": 5, "evergreen": 2},
        "candidates": [
            _candidate(
                "announcement",
                "event",
                attention=5,
                demand=5,
                complete_bridge=False,
            )
        ],
    }

    report = rank_opportunities(payload)

    assert report.selected_id is None
    candidate = report.candidates[0]
    assert not candidate.eligible
    assert candidate.editorial_route == "daily-digest"
    assert candidate.business_bridge_completeness < 1
    assert "business bridge is incomplete" in candidate.blockers


def test_incomplete_business_case_cannot_be_selected_for_longform():
    payload = {
        "candidates": [
            _candidate(
                "vague-demand",
                "demand",
                attention=5,
                demand=5,
                complete_case=False,
            )
        ]
    }

    report = rank_opportunities(payload)

    assert report.selected_id is None
    candidate = report.candidates[0]
    assert not candidate.eligible
    assert "business case completeness below 0.75" in candidate.blockers


def test_high_risk_candidate_is_blocked():
    report = rank_opportunities(
        {"candidates": [_candidate("rumor", "event", attention=5, demand=5, risk=4)]}
    )

    assert report.selected_id is None
    assert "risk above 2" in report.candidates[0].blockers


def test_cli_writes_ranked_report(tmp_path):
    source = tmp_path / "topic-candidates.json"
    output = tmp_path / "opportunity-report.json"
    bridge_output = tmp_path / "business-bridge.json"
    source.write_text(
        json.dumps(
            {"candidates": [_candidate("real-project", "demand", attention=3, demand=5)]}
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            str(source),
            "--json-out",
            str(output),
            "--bridge-out",
            str(bridge_output),
        ]
    )

    assert exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["selected_id"] == "real-project"
    bridge = json.loads(bridge_output.read_text(encoding="utf-8"))
    assert bridge["topic_id"] == "real-project"
    assert bridge["status"] == "ready-for-deep-research"
    assert bridge["business_bridge"]["technical_delta"]


def test_screenshot_demo_selects_real_demand_over_event_spike():
    root = Path(__file__).resolve().parents[2]
    payload = json.loads(
        (root / "examples/topic-opportunity-demo/topic-candidates.json").read_text(
            encoding="utf-8"
        )
    )

    report = rank_opportunities(payload)

    assert report.selected_id == "ai-search-build-or-buy"
    assert report.selected_track == "demand"
