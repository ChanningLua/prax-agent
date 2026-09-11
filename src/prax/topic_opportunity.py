"""Rank article ideas by demand, attention, editorial utility, and portfolio fit."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from prax.article_business import business_bridge_details

TRACKS = ("event", "demand", "evergreen")
DEFAULT_TARGET_MIX = {"event": 0.3, "demand": 0.5, "evergreen": 0.2}
BUSINESS_CASE_FIELDS = (
    "actor",
    "job_to_be_done",
    "trigger",
    "current_workaround",
    "decision",
    "cost_of_inaction",
)
DEMAND_WEIGHTS = {
    "problem_frequency": 0.30,
    "decision_urgency": 0.25,
    "scenario_specificity": 0.20,
    "evidence_strength": 0.15,
    "audience_fit": 0.10,
}
ATTENTION_WEIGHTS = {
    "attention_velocity": 0.55,
    "cross_source": 0.45,
}
UTILITY_WEIGHTS = {
    "depth": 0.35,
    "content_gap": 0.30,
    "evidence_strength": 0.20,
    "audience_fit": 0.15,
}


@dataclass(frozen=True)
class CandidateScore:
    id: str
    title: str
    track: str
    score: float
    demand_score: float
    attention_score: float
    editorial_utility_score: float
    penalty: float
    business_case_completeness: float
    business_bridge_completeness: float
    missing_business_bridge_fields: list[str]
    editorial_route: str
    eligible: bool
    blockers: list[str]


@dataclass(frozen=True)
class OpportunityReport:
    selected_id: str | None
    selected_track: str | None
    selection_reason: str
    target_mix: dict[str, float]
    portfolio_counts: dict[str, int]
    track_deficits: dict[str, float]
    candidates: list[CandidateScore]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _bounded_signal(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(5.0, max(0.0, number))


def _weighted_score(signals: dict[str, Any], weights: dict[str, float]) -> float:
    return 100 * sum(
        weight * (_bounded_signal(signals.get(name)) / 5)
        for name, weight in weights.items()
    )


def _business_case_completeness(candidate: dict[str, Any]) -> float:
    business_case = candidate.get("business_case", {})
    if not isinstance(business_case, dict):
        return 0.0
    present = sum(
        bool(str(business_case.get(name, "")).strip())
        for name in BUSINESS_CASE_FIELDS
    )
    evidence = business_case.get("evidence", [])
    evidence_present = isinstance(evidence, list) and any(
        str(item).strip() for item in evidence
    )
    return round((present + int(evidence_present)) / (len(BUSINESS_CASE_FIELDS) + 1), 2)


def score_candidate(candidate: dict[str, Any]) -> CandidateScore:
    candidate_id = str(candidate.get("id", "")).strip()
    title = str(candidate.get("title", "")).strip()
    track = str(candidate.get("track", "")).strip().lower()
    signals_value = candidate.get("signals", {})
    signals = signals_value if isinstance(signals_value, dict) else {}

    demand_score = _weighted_score(signals, DEMAND_WEIGHTS)
    attention_score = _weighted_score(signals, ATTENTION_WEIGHTS)
    utility_score = _weighted_score(signals, UTILITY_WEIGHTS)
    risk = _bounded_signal(signals.get("risk"))
    saturation = _bounded_signal(signals.get("saturation"))
    penalty = (risk / 5 * 6) + (saturation / 5 * 4)
    score = round(
        max(
            0.0,
            min(
                100.0,
                (0.45 * demand_score)
                + (0.20 * attention_score)
                + (0.35 * utility_score)
                - penalty,
            ),
        ),
        1,
    )
    completeness = _business_case_completeness(candidate)
    bridge_completeness, missing_bridge_fields = business_bridge_details(candidate)

    blockers: list[str] = []
    if not candidate_id:
        blockers.append("missing id")
    if not title:
        blockers.append("missing title")
    if track not in TRACKS:
        blockers.append(f"track must be one of {', '.join(TRACKS)}")
    if score < 65:
        blockers.append("opportunity score below 65")
    if _bounded_signal(signals.get("evidence_strength")) < 3:
        blockers.append("evidence_strength below 3")
    if risk > 2:
        blockers.append("risk above 2")
    if completeness < 0.75:
        blockers.append("business case completeness below 0.75")
    if missing_bridge_fields:
        blockers.append("business bridge is incomplete")

    if not blockers:
        editorial_route = "longform"
    elif (
        track == "event"
        and "business bridge is incomplete" in blockers
        and risk <= 2
    ):
        editorial_route = "daily-digest"
    elif risk > 2 or not candidate_id or not title or track not in TRACKS:
        editorial_route = "hold"
    else:
        editorial_route = "research-brief"

    return CandidateScore(
        id=candidate_id,
        title=title,
        track=track,
        score=score,
        demand_score=round(demand_score, 1),
        attention_score=round(attention_score, 1),
        editorial_utility_score=round(utility_score, 1),
        penalty=round(penalty, 1),
        business_case_completeness=completeness,
        business_bridge_completeness=bridge_completeness,
        missing_business_bridge_fields=missing_bridge_fields,
        editorial_route=editorial_route,
        eligible=not blockers,
        blockers=blockers,
    )


def _normalized_mix(payload: Any) -> dict[str, float]:
    values = payload if isinstance(payload, dict) else {}
    mix = {
        track: max(0.0, float(values.get(track, DEFAULT_TARGET_MIX[track])))
        for track in TRACKS
    }
    total = sum(mix.values())
    if total <= 0:
        return dict(DEFAULT_TARGET_MIX)
    return {track: round(value / total, 4) for track, value in mix.items()}


def _portfolio_counts(payload: Any) -> dict[str, int]:
    values = payload if isinstance(payload, dict) else {}
    result = {}
    for track in TRACKS:
        try:
            result[track] = max(0, int(values.get(track, 0)))
        except (TypeError, ValueError):
            result[track] = 0
    return result


def rank_opportunities(payload: Any) -> OpportunityReport:
    if not isinstance(payload, dict):
        raise TypeError("input must be a JSON object")
    candidates_value = payload.get("candidates", [])
    if not isinstance(candidates_value, list):
        raise TypeError("candidates must be a JSON array")

    candidates = [
        score_candidate(candidate)
        for candidate in candidates_value
        if isinstance(candidate, dict)
    ]
    candidates.sort(key=lambda item: (-item.score, item.id))
    target_mix = _normalized_mix(payload.get("target_mix"))
    counts = _portfolio_counts(payload.get("portfolio_counts"))
    total = sum(counts.values())
    actual_mix = {
        track: (counts[track] / total if total else 0.0) for track in TRACKS
    }
    deficits = {
        track: round(target_mix[track] - actual_mix[track], 4) for track in TRACKS
    }

    eligible = [candidate for candidate in candidates if candidate.eligible]
    selected: CandidateScore | None = None
    reason = "no eligible candidate"
    underfilled_tracks = sorted(
        (track for track in TRACKS if deficits[track] > 0),
        key=lambda track: (-deficits[track], -target_mix[track], track),
    )
    if eligible and underfilled_tracks:
        priority = underfilled_tracks
        for track in priority:
            track_candidates = [
                candidate for candidate in eligible if candidate.track == track
            ]
            if track_candidates:
                selected = track_candidates[0]
                reason = (
                    f"selected best eligible {track} candidate to close portfolio "
                    f"deficit {deficits[track]:.2f}"
                )
                break
        if selected is None:
            selected = eligible[0]
            reason = (
                "no eligible candidate in an underfilled track; selected highest "
                "opportunity score"
            )
    elif eligible:
        selected = eligible[0]
        reason = "portfolio is on target; selected highest opportunity score"

    return OpportunityReport(
        selected_id=selected.id if selected else None,
        selected_track=selected.track if selected else None,
        selection_reason=reason,
        target_mix=target_mix,
        portfolio_counts=counts,
        track_deficits=deficits,
        candidates=candidates,
    )


def build_business_bridge_artifact(
    payload: dict[str, Any], report: OpportunityReport
) -> dict[str, Any]:
    """Build the selected topic's auditable signal-to-business handoff."""

    if report.selected_id is None:
        return {
            "topic_id": None,
            "status": "no-longform-topic",
            "business_case": {},
            "business_bridge": {},
        }
    raw_candidates = payload.get("candidates", [])
    candidate = next(
        (
            item
            for item in raw_candidates
            if isinstance(item, dict)
            and str(item.get("id", "")).strip() == report.selected_id
        ),
        {},
    )
    business_case_value = candidate.get("business_case", {})
    business_bridge_value = candidate.get("business_bridge", {})
    business_case = (
        business_case_value if isinstance(business_case_value, dict) else {}
    )
    business_bridge = (
        business_bridge_value if isinstance(business_bridge_value, dict) else {}
    )
    return {
        "topic_id": report.selected_id,
        "title": str(candidate.get("title", "")).strip(),
        "track": report.selected_track,
        "status": "ready-for-deep-research",
        "business_case": {
            "actor": business_case.get("actor", ""),
            "job_to_be_done": business_case.get("job_to_be_done", ""),
            "decision": business_case.get("decision", ""),
            "cost_of_inaction": business_case.get("cost_of_inaction", ""),
        },
        "business_bridge": business_bridge,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rank article ideas using demand, attention, and portfolio fit."
    )
    parser.add_argument("input", type=Path, help="Path to topic-candidates.json")
    parser.add_argument("--json-out", type=Path, help="Write report to JSON")
    parser.add_argument(
        "--bridge-out",
        type=Path,
        help="Write selected topic's business-bridge.json",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        report = rank_opportunities(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"topic-opportunity: {exc}", file=sys.stderr)
        return 2

    rendered = json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered + "\n", encoding="utf-8")
    if args.bridge_out:
        bridge = build_business_bridge_artifact(payload, report)
        args.bridge_out.parent.mkdir(parents=True, exist_ok=True)
        args.bridge_out.write_text(
            json.dumps(bridge, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(rendered)
    return 0 if report.selected_id else 1


if __name__ == "__main__":
    raise SystemExit(main())
