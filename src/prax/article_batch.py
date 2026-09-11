"""Plan a portfolio-balanced batch of evidence-backed articles."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from prax.topic_opportunity import (
    build_business_bridge_artifact,
    rank_opportunities,
)


@dataclass(frozen=True)
class BatchSlot:
    position: int
    topic_id: str
    title: str
    track: str
    opportunity_score: float
    workspace: str
    status: str


@dataclass(frozen=True)
class BatchManifest:
    batch_date: str
    requested_count: int
    planned_count: int
    complete: bool
    calibration_gate: dict[str, Any]
    track_counts: dict[str, int]
    slots: list[BatchSlot]
    unplanned: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-._").lower()
    return slug[:80] or "article"


def _candidate_by_id(payload: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    for candidate in payload.get("candidates", []):
        if isinstance(candidate, dict) and str(candidate.get("id", "")) == candidate_id:
            return candidate
    raise ValueError(f"selected candidate is missing from input: {candidate_id}")


def _research_plan(candidate: dict[str, Any]) -> dict[str, Any]:
    bridge = candidate.get("business_bridge", {})
    business_case = candidate.get("business_case", {})
    return {
        "topic_id": candidate.get("id"),
        "research_questions": [
            {
                "id": "R1",
                "question": "原始信号究竟确认了什么，哪些仍是推断？",
                "target": bridge.get("source_signal", ""),
                "preferred_sources": ["official", "repository", "paper"],
            },
            {
                "id": "R2",
                "question": "技术能力、约束或架构相对之前具体变了什么？",
                "target": bridge.get("technical_delta", ""),
                "preferred_sources": ["documentation", "repository", "paper", "standard"],
            },
            {
                "id": "R3",
                "question": "变化落在业务流程的哪个节点，谁承担结果？",
                "target": bridge.get("affected_workflow", ""),
                "preferred_sources": ["customer-case", "issue", "interview", "workflow"],
            },
            {
                "id": "R4",
                "question": "决策人有哪些真实选项，各自成本和失败条件是什么？",
                "target": bridge.get("business_decision", ""),
                "preferred_sources": ["documentation", "pricing", "benchmark", "case-study"],
            },
            {
                "id": "R5",
                "question": "如何做一次与业务决策直接相关的可复现验证？",
                "target": bridge.get("validation_method", ""),
                "success_metric": bridge.get("success_metric", ""),
                "preferred_sources": ["repository", "dataset", "standard"],
            },
            {
                "id": "R6",
                "question": "哪些情况下不应该采用这项技术或方案？",
                "target": bridge.get("no_fit_conditions", ""),
                "preferred_sources": ["limitations", "incident", "counterexample"],
            },
        ],
        "minimum_sources": 20,
        "minimum_primary_sources": 4,
        "minimum_demand_signal_types": 2,
        "actor": business_case.get("actor", ""),
        "decision": business_case.get("decision", ""),
    }


def _brief_markdown(candidate: dict[str, Any], position: int) -> str:
    business_case = candidate.get("business_case", {})
    bridge = candidate.get("business_bridge", {})
    return "\n".join(
        (
            f"# 批次文章 {position:02d}：{candidate.get('title', '')}",
            "",
            f"- 轨道：`{candidate.get('track', '')}`",
            f"- 读者：{business_case.get('actor', '')}",
            f"- 任务：{business_case.get('job_to_be_done', '')}",
            f"- 当前办法：{business_case.get('current_workaround', '')}",
            f"- 待做决策：{business_case.get('decision', '')}",
            f"- 不处理的代价：{business_case.get('cost_of_inaction', '')}",
            "",
            "## 技术到业务链",
            "",
            f"- 来源信号：{bridge.get('source_signal', '')}",
            f"- 技术变化：{bridge.get('technical_delta', '')}",
            f"- 受影响流程：{bridge.get('affected_workflow', '')}",
            f"- 业务决策：{bridge.get('business_decision', '')}",
            f"- 验证方法：{bridge.get('validation_method', '')}",
            f"- 成功指标：{bridge.get('success_metric', '')}",
            f"- 不适用条件：{bridge.get('no_fit_conditions', '')}",
            "",
            "## 交付要求",
            "",
            (
                "运行 `hotspot-article`，完成来源索引、真实验证、事实核验和质量门；"
                "在全部 hard gates 通过前不得生成 `publish-ready.md`。"
            ),
            "",
        )
    )


def plan_batch(
    payload: dict[str, Any],
    *,
    count: int,
    batch_date: str,
    output_root: Path,
) -> BatchManifest:
    if not 1 <= count <= 20:
        raise ValueError("count must be between 1 and 20")
    candidate_values = payload.get("candidates", [])
    if not isinstance(candidate_values, list):
        raise TypeError("candidates must be an array")

    remaining = [
        candidate for candidate in candidate_values if isinstance(candidate, dict)
    ]
    counts_value = payload.get("portfolio_counts", {})
    counts = {
        track: max(0, int(counts_value.get(track, 0)))
        if isinstance(counts_value, dict)
        else 0
        for track in ("event", "demand", "evergreen")
    }
    slots: list[BatchSlot] = []

    for position in range(1, count + 1):
        iteration = {
            "target_mix": payload.get("target_mix", {}),
            "portfolio_counts": counts,
            "candidates": remaining,
        }
        report = rank_opportunities(iteration)
        if report.selected_id is None:
            break
        candidate = _candidate_by_id(iteration, report.selected_id)
        scored = next(
            item for item in report.candidates if item.id == report.selected_id
        )
        slug = _safe_slug(report.selected_id)
        workspace = output_root / f"{position:02d}-{slug}"
        workspace.mkdir(parents=True, exist_ok=True)

        bridge_artifact = build_business_bridge_artifact(iteration, report)
        (workspace / "business-bridge.json").write_text(
            json.dumps(bridge_artifact, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (workspace / "research-plan.json").write_text(
            json.dumps(_research_plan(candidate), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (workspace / "brief.md").write_text(
            _brief_markdown(candidate, position),
            encoding="utf-8",
        )
        stage = "calibration-pilot" if position == 1 else "waiting-for-calibration"
        next_action = (
            "complete one full article and record batch calibration"
            if position == 1
            else "wait until calibration-gate.json is released"
        )
        status = {
            "topic_id": report.selected_id,
            "stage": stage,
            "publish_ready": False,
            "next_action": next_action,
        }
        (workspace / "status.json").write_text(
            json.dumps(status, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        slots.append(
            BatchSlot(
                position=position,
                topic_id=report.selected_id,
                title=scored.title,
                track=scored.track,
                opportunity_score=scored.score,
                workspace=str(workspace),
                status=stage,
            )
        )
        counts[scored.track] += 1
        remaining = [
            item for item in remaining if str(item.get("id", "")) != report.selected_id
        ]

    final_report = rank_opportunities(
        {
            "target_mix": payload.get("target_mix", {}),
            "portfolio_counts": counts,
            "candidates": remaining,
        }
    )
    unplanned = [
        {
            "id": candidate.id,
            "title": candidate.title,
            "track": candidate.track,
            "score": candidate.score,
            "editorial_route": candidate.editorial_route,
            "blockers": candidate.blockers,
        }
        for candidate in final_report.candidates
    ]
    planned_track_counts = {
        track: sum(slot.track == track for slot in slots)
        for track in ("event", "demand", "evergreen")
    }
    pilot_topic_id = slots[0].topic_id if slots else None
    calibration_gate = {
        "required": count > 1,
        "status": "pending" if count > 1 else "not-required",
        "pilot_topic_id": pilot_topic_id,
        "release_conditions": [
            "pilot hard gates pass",
            "editorial review scores all reach 4/5",
            "evidence limitations are explicit",
            "batch prompts and gates are updated from observed pilot failures",
        ],
        "remaining_slots_released": count <= 1,
    }
    (output_root / "calibration-gate.json").write_text(
        json.dumps(calibration_gate, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return BatchManifest(
        batch_date=batch_date,
        requested_count=count,
        planned_count=len(slots),
        complete=len(slots) == count,
        calibration_gate=calibration_gate,
        track_counts=planned_track_counts,
        slots=slots,
        unplanned=unplanned,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Plan a batch of long-form articles")
    parser.add_argument("input", type=Path, help="Path to topic-candidates.json")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--date", default=datetime.now().astimezone().date().isoformat())
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Allow writing into a batch directory that already has a manifest",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest_path = args.out_root / "batch-manifest.json"
    if manifest_path.exists() and not args.resume:
        print(
            f"article-batch: {manifest_path} already exists; pass --resume to continue",
            file=sys.stderr,
        )
        return 2
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("input must be a JSON object")
        args.out_root.mkdir(parents=True, exist_ok=True)
        manifest = plan_batch(
            payload,
            count=args.count,
            batch_date=args.date,
            output_root=args.out_root,
        )
        rendered = json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2)
        manifest_path.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 0 if manifest.complete else 1
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        print(f"article-batch: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
