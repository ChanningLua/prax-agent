"""Deterministic quality gate for evidence-backed long-form articles.

The gate intentionally measures auditable editorial properties, not prose taste.
It is designed to be paired with an LLM/editor review described by the bundled
``hotspot-article`` skill.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from prax.article_business import business_bridge_details

PRIMARY_SOURCE_TYPES = {
    "documentation",
    "official",
    "paper",
    "repository",
    "standard",
    "standards",
}
ACCEPTED_FACT_STATUSES = {"qualified", "supported", "verified"}
ACCEPTED_EVIDENCE_STATUSES = {"passed", "success", "verified"}
FORMULAIC_STYLE_PATTERNS = (
    r"(?m)^\s*(?:首先|其次|再次|最后)[，,]",
    r"(?m)^\s*(?:第一|第二|第三|第四|第五)(?:条|点|个|类|方面)[，,是：:]",
    r"不(?:仅|只).{0,30}(?:还|更|也)",
    r"不是.{0,50}而是",
    r"(?:值得注意|需要指出|必须强调|毋庸置疑|显而易见|不难发现)(?:的是)?",
    r"(?:综上所述|总而言之|从某种意义上说)",
)


@dataclass(frozen=True)
class QualityProfile:
    name: str
    min_content_chars: int
    max_content_chars: int
    min_sections: int
    max_sections: int
    min_sources: int
    min_cited_sources: int
    min_cited_primary_sources: int
    min_source_domains: int
    min_fact_checked_claims: int
    min_evidence_runs: int
    min_score: int
    min_visuals: int = 1


SELECTED_PROFILE = QualityProfile(
    name="selected",
    min_content_chars=8_000,
    max_content_chars=15_000,
    min_sections=10,
    max_sections=18,
    min_sources=20,
    min_cited_sources=8,
    min_cited_primary_sources=4,
    min_source_domains=6,
    min_fact_checked_claims=8,
    min_evidence_runs=1,
    min_score=80,
)
SELECTED_SHARP_PROFILE = QualityProfile(
    name="selected-sharp",
    min_content_chars=3_500,
    max_content_chars=6_500,
    min_sections=5,
    max_sections=9,
    min_sources=10,
    min_cited_sources=6,
    min_cited_primary_sources=4,
    min_source_domains=4,
    min_fact_checked_claims=8,
    min_evidence_runs=1,
    min_score=80,
)
SELECTED_ANALYSIS_PROFILE = QualityProfile(
    name="selected-analysis",
    min_content_chars=6_000,
    max_content_chars=12_000,
    min_sections=5,
    max_sections=9,
    min_sources=12,
    min_cited_sources=7,
    min_cited_primary_sources=4,
    min_source_domains=4,
    min_fact_checked_claims=10,
    min_evidence_runs=1,
    min_score=80,
    min_visuals=2,
)
SELECTED_FRAMEWORK_PROFILE = QualityProfile(
    name="selected-framework",
    min_content_chars=5_500,
    max_content_chars=11_000,
    min_sections=5,
    max_sections=9,
    min_sources=8,
    min_cited_sources=5,
    min_cited_primary_sources=3,
    min_source_domains=3,
    min_fact_checked_claims=8,
    min_evidence_runs=1,
    min_score=80,
    min_visuals=2,
)
SELECTED_HANDS_ON_PROFILE = QualityProfile(
    name="selected-hands-on",
    min_content_chars=4_500,
    max_content_chars=9_000,
    min_sections=5,
    max_sections=9,
    min_sources=4,
    min_cited_sources=3,
    min_cited_primary_sources=2,
    min_source_domains=2,
    min_fact_checked_claims=5,
    min_evidence_runs=2,
    min_score=80,
    min_visuals=4,
)
DEFAULT_PROFILE = SELECTED_ANALYSIS_PROFILE
PROFILES = {
    SELECTED_PROFILE.name: SELECTED_PROFILE,
    SELECTED_SHARP_PROFILE.name: SELECTED_SHARP_PROFILE,
    SELECTED_ANALYSIS_PROFILE.name: SELECTED_ANALYSIS_PROFILE,
    SELECTED_FRAMEWORK_PROFILE.name: SELECTED_FRAMEWORK_PROFILE,
    SELECTED_HANDS_ON_PROFILE.name: SELECTED_HANDS_ON_PROFILE,
}


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    actual: Any
    expected: str


@dataclass(frozen=True)
class QualityReport:
    profile: str
    passed: bool
    score: int
    score_out_of_ten: float
    metrics: dict[str, Any]
    category_scores: dict[str, int]
    gates: list[GateResult]
    recommendations: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read_json(path: Path | None, label: str) -> Any:
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{label} file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {path}: {exc}") from exc


def _records(payload: Any, key: str) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, list):
        values = payload
    elif isinstance(payload, dict):
        values = payload.get(key, [])
    else:
        values = []
    return [item for item in values if isinstance(item, dict)]


def _without_frontmatter(markdown: str) -> str:
    if not markdown.startswith("---"):
        return markdown
    match = re.match(r"\A---\s*\n.*?\n---\s*(?:\n|\Z)", markdown, re.DOTALL)
    return markdown[match.end() :] if match else markdown


def _prose_only(markdown: str) -> str:
    text = _without_frontmatter(markdown)
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"`[^`\n]+`", "", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)
    text = re.sub(r"(?m)^\s*[-*+>]\s+", "", text)
    text = re.sub(r"[*_~|]", "", text)
    return text


def _effective_char_count(markdown: str) -> int:
    return len(re.sub(r"\s+", "", _prose_only(markdown)))


def _h2_titles(markdown: str) -> list[str]:
    return [
        title.strip()
        for title in re.findall(r"(?m)^\s{0,3}##(?!#)\s+(.+?)\s*$", markdown)
    ]


def _has_heading(titles: Iterable[str], keywords: Iterable[str]) -> bool:
    lowered = tuple(keyword.lower() for keyword in keywords)
    return any(any(keyword in title.lower() for keyword in lowered) for title in titles)


def _visual_count(markdown: str) -> int:
    tables = len(
        re.findall(r"(?m)^\s*\|(?:\s*:?-{3,}:?\s*\|){2,}\s*$", markdown)
    )
    mermaid = len(re.findall(r"(?i)```\s*mermaid\b", markdown))
    images = len(re.findall(r"!\[[^\]]*\]\([^)]+\)", markdown))
    return tables + mermaid + images


def _paragraph_lengths(markdown: str) -> list[int]:
    prose = _prose_only(markdown)
    paragraphs = [
        len(re.sub(r"\s+", "", value))
        for value in re.split(r"\n\s*\n", prose)
        if value.strip()
    ]
    return [length for length in paragraphs if length > 0]


def _source_domain(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _is_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _article_body_before_references(markdown: str) -> str:
    match = re.search(
        r"(?im)^\s{0,3}##(?!#)\s+.*(?:参考|来源|引用|references|sources).*$",
        markdown,
    )
    return markdown[: match.start()] if match else markdown


def _unresolved_marker_count(markdown: str) -> int:
    """Count editorial placeholders without flagging prose that discusses them."""
    without_code = re.sub(r"```.*?```", "", markdown, flags=re.DOTALL)
    line_markers = re.findall(
        r"(?im)^\s*(?:[-*]\s*)?(?:\[[ x]?\]\s*)?"
        r"(?:TODO|FIXME|citation needed|待核验|待查证|来源待补)"
        r"(?:\s*[:：-].*)?$",
        without_code,
    )
    bracketed_markers = re.findall(
        r"(?i)\[(?:TODO|FIXME|citation needed|待核验|待查证|来源待补)\]",
        without_code,
    )
    return len(line_markers) + len(bracketed_markers)


def _formulaic_style_count(markdown: str) -> int:
    prose = _prose_only(markdown)
    return sum(
        len(re.findall(pattern, prose, flags=re.DOTALL))
        for pattern in FORMULAIC_STYLE_PATTERNS
    )


def _bounded_ratio(actual: int, target: int) -> float:
    if target <= 0:
        return 1.0
    return min(1.0, max(0.0, actual / target))


def evaluate_article(
    article_markdown: str,
    *,
    source_payload: Any = None,
    fact_payload: Any = None,
    evidence_payload: Any = None,
    business_bridge_payload: Any = None,
    evidence_root: Path | None = None,
    profile: QualityProfile = SELECTED_PROFILE,
) -> QualityReport:
    """Evaluate one article and its machine-readable editorial manifests."""

    sources = _records(source_payload, "sources")
    claims = _records(fact_payload, "claims")
    evidence_runs = _records(evidence_payload, "runs")
    titles = _h2_titles(article_markdown)
    bridge_completeness, missing_bridge_fields = business_bridge_details(
        business_bridge_payload
    )

    source_by_id: dict[str, dict[str, Any]] = {}
    seen_urls: set[str] = set()
    rejected_source_count = 0
    for source in sources:
        source_id = str(source.get("id", "")).strip()
        url = str(source.get("url", "")).strip()
        normalized_url = url.rstrip("/")
        if (
            not source_id
            or source_id in source_by_id
            or not _is_http_url(url)
            or normalized_url in seen_urls
        ):
            rejected_source_count += 1
            continue
        source_by_id[source_id] = source
        seen_urls.add(normalized_url)

    article_body = _article_body_before_references(article_markdown)
    cited_source_ids = sorted(
        source_id
        for source_id in source_by_id
        if re.search(rf"\[{re.escape(source_id)}\]", article_body)
    )
    cited_primary_ids = [
        source_id
        for source_id in cited_source_ids
        if str(source_by_id[source_id].get("source_type", "")).lower()
        in PRIMARY_SOURCE_TYPES
    ]
    researched_domains = {
        domain
        for source in source_by_id.values()
        if (domain := _source_domain(str(source.get("url", ""))))
    }
    cited_domains = {
        domain
        for source_id in cited_source_ids
        if (
            domain := _source_domain(
                str(source_by_id[source_id].get("url", ""))
            )
        )
    }

    def claim_is_supported(claim: dict[str, Any]) -> bool:
        linked = claim.get("source_ids", [])
        source_ids = linked if isinstance(linked, list) else []
        return (
            str(claim.get("status", "")).lower() in ACCEPTED_FACT_STATUSES
            and bool(source_ids)
            and all(str(source_id) in source_by_id for source_id in source_ids)
        )

    accepted_claims = [claim for claim in claims if claim_is_supported(claim)]
    unresolved_claims = [
        claim
        for claim in claims
        if not claim_is_supported(claim) and bool(claim.get("included_in_article", True))
    ]
    def evidence_run_is_accepted(run: dict[str, Any]) -> bool:
        if (
            str(run.get("status", "")).lower() not in ACCEPTED_EVIDENCE_STATUSES
            or not bool(run.get("artifact"))
            or not bool(run.get("result"))
            or not bool(run.get("command") or run.get("method"))
            or not bool(run.get("limitations"))
        ):
            return False
        if evidence_root is None:
            return True
        root = evidence_root.resolve()
        artifact = (root / str(run["artifact"])).resolve()
        return artifact.is_relative_to(root) and artifact.is_file()

    accepted_runs = [run for run in evidence_runs if evidence_run_is_accepted(run)]
    missing_evidence_artifacts = [
        str(run.get("artifact", ""))
        for run in evidence_runs
        if str(run.get("status", "")).lower() in ACCEPTED_EVIDENCE_STATUSES
        and not evidence_run_is_accepted(run)
    ]

    content_chars = _effective_char_count(article_markdown)
    section_count = len(titles)
    visual_count = _visual_count(article_markdown)
    evidence_section = _has_heading(
        titles, ("实测", "复现", "验证", "实验", "测试", "评测")
    )
    limitations_section = _has_heading(
        titles,
        ("局限", "限制", "风险", "失效", "不适用", "反例", "挡不住", "没覆盖"),
    )
    references_section = _has_heading(
        titles, ("参考", "来源", "引用", "references", "sources")
    )
    mechanism_section = _has_heading(titles, ("原理", "机制", "背景", "问题"))
    implementation_section = _has_heading(
        titles, ("方案", "步骤", "实现", "实践", "怎么做")
    )
    case_section = _has_heading(titles, ("案例", "场景", "样例", "实践"))
    unresolved_markers = _unresolved_marker_count(article_markdown)
    failure_mentions = len(
        re.findall(r"失败|失效|反例|误差|偏差|不适用|代价|风险", article_markdown)
    )
    hype_mentions = len(
        re.findall(r"颠覆性|史诗级|遥遥领先|彻底改变|震撼发布|秒杀", article_markdown)
    )
    formulaic_style_count = _formulaic_style_count(article_markdown)
    paragraph_lengths = _paragraph_lengths(article_body)
    longest_paragraph = max(paragraph_lengths, default=0)

    metrics: dict[str, Any] = {
        "content_chars": content_chars,
        "section_count": section_count,
        "researched_source_count": len(source_by_id),
        "rejected_or_duplicate_source_count": rejected_source_count,
        "cited_source_count": len(cited_source_ids),
        "cited_primary_source_count": len(cited_primary_ids),
        "source_domain_count": len(cited_domains),
        "cited_source_domain_count": len(cited_domains),
        "researched_source_domain_count": len(researched_domains),
        "fact_checked_claim_count": len(accepted_claims),
        "unresolved_claim_count": len(unresolved_claims),
        "accepted_evidence_run_count": len(accepted_runs),
        "missing_or_invalid_evidence_artifacts": missing_evidence_artifacts,
        "business_bridge_completeness": bridge_completeness,
        "missing_business_bridge_fields": missing_bridge_fields,
        "visual_count": visual_count,
        "unresolved_marker_count": unresolved_markers,
        "longest_paragraph_chars": longest_paragraph,
        "hype_phrase_count": hype_mentions,
        "formulaic_style_count": formulaic_style_count,
        "has_evidence_section": evidence_section,
        "has_limitations_section": limitations_section,
        "has_references_section": references_section,
        "cited_source_ids": cited_source_ids,
        "cited_primary_source_ids": cited_primary_ids,
    }

    gates = [
        GateResult(
            "content_chars",
            profile.min_content_chars <= content_chars <= profile.max_content_chars,
            content_chars,
            f"{profile.min_content_chars}..{profile.max_content_chars}",
        ),
        GateResult(
            "section_count",
            profile.min_sections <= section_count <= profile.max_sections,
            section_count,
            f"{profile.min_sections}..{profile.max_sections} H2 sections",
        ),
        GateResult(
            "researched_sources",
            len(source_by_id) >= profile.min_sources,
            len(source_by_id),
            f">={profile.min_sources}",
        ),
        GateResult(
            "cited_sources",
            len(cited_source_ids) >= profile.min_cited_sources,
            len(cited_source_ids),
            f">={profile.min_cited_sources}",
        ),
        GateResult(
            "cited_primary_sources",
            len(cited_primary_ids) >= profile.min_cited_primary_sources,
            len(cited_primary_ids),
            f">={profile.min_cited_primary_sources}",
        ),
        GateResult(
            "source_domains",
            len(cited_domains) >= profile.min_source_domains,
            len(cited_domains),
            f">={profile.min_source_domains} cited domains",
        ),
        GateResult(
            "fact_check_manifest",
            fact_payload is not None
            and len(accepted_claims) >= profile.min_fact_checked_claims
            and not unresolved_claims,
            {
                "checked": len(accepted_claims),
                "unresolved": len(unresolved_claims),
            },
            f">={profile.min_fact_checked_claims} checked and 0 unresolved",
        ),
        GateResult(
            "evidence_manifest",
            len(accepted_runs) >= profile.min_evidence_runs,
            len(accepted_runs),
            f">={profile.min_evidence_runs} reproducible run",
        ),
        GateResult(
            "business_bridge",
            not missing_bridge_fields,
            {
                "completeness": bridge_completeness,
                "missing": missing_bridge_fields,
            },
            "all signal-to-business fields present",
        ),
        GateResult("evidence_section", evidence_section, evidence_section, "present"),
        GateResult(
            "limitations_section", limitations_section, limitations_section, "present"
        ),
        GateResult(
            "references_section", references_section, references_section, "present"
        ),
        GateResult(
            "visual",
            visual_count >= profile.min_visuals,
            visual_count,
            f">={profile.min_visuals} table/diagram/image",
        ),
        GateResult(
            "no_unresolved_markers", unresolved_markers == 0, unresolved_markers, "0"
        ),
    ]

    volume_score = round(
        10 * _bounded_ratio(content_chars, profile.min_content_chars)
    )
    if content_chars > profile.max_content_chars:
        volume_score = 8

    covered_structure = sum(
        (
            mechanism_section,
            implementation_section,
            case_section,
            limitations_section,
            evidence_section,
        )
    )
    structure_score = (
        round(6 * _bounded_ratio(section_count, profile.min_sections))
        + covered_structure
        + (2 if references_section else 0)
        + (2 if profile.min_sections <= section_count <= profile.max_sections else 0)
    )
    structure_score = min(15, structure_score)

    sources_score = (
        round(8 * _bounded_ratio(len(source_by_id), profile.min_sources))
        + round(7 * _bounded_ratio(len(cited_source_ids), profile.min_cited_sources))
        + round(
            5
            * _bounded_ratio(
                len(cited_primary_ids), profile.min_cited_primary_sources
            )
        )
        + round(5 * _bounded_ratio(len(cited_domains), profile.min_source_domains))
    )
    evidence_score = (
        round(8 * _bounded_ratio(len(accepted_runs), profile.min_evidence_runs))
        + (4 if evidence_section else 0)
        + (3 if visual_count else 0)
    )
    fact_score = (
        (5 if fact_payload is not None else 0)
        + round(
            6
            * _bounded_ratio(
                len(accepted_claims), profile.min_fact_checked_claims
            )
        )
        + (4 if not unresolved_claims and fact_payload is not None else 0)
    )
    limitations_score = (
        (6 if limitations_section else 0) + min(4, failure_mentions)
    )
    editorial_score = (
        (4 if unresolved_markers == 0 else 0)
        + (2 if longest_paragraph <= 600 else 1 if longest_paragraph <= 900 else 0)
        + (2 if hype_mentions == 0 else 1 if hype_mentions <= 2 else 0)
        + (
            2
            if formulaic_style_count <= 8
            else 1
            if formulaic_style_count <= 14
            else 0
        )
    )
    category_scores = {
        "volume": volume_score,
        "structure": structure_score,
        "sources": min(25, sources_score),
        "evidence": min(15, evidence_score),
        "fact_check": min(15, fact_score),
        "limitations": min(10, limitations_score),
        "editorial_hygiene": min(10, editorial_score),
    }
    score = min(100, sum(category_scores.values()))
    all_gates_passed = all(gate.passed for gate in gates)
    passed = all_gates_passed and score >= profile.min_score

    recommendation_map = {
        "content_chars": (
            f"把正文有效字符控制在 {profile.min_content_chars:,}–"
            f"{profile.max_content_chars:,}，优先补证据和推理，不要注水。"
        ),
        "section_count": (
            f"用 {profile.min_sections}–{profile.max_sections} 个 H2 组织主线；"
            "H3 不计入章节门槛。"
        ),
        "researched_sources": (
            f"研究池补到至少 {profile.min_sources} 个有 URL 的唯一来源。"
        ),
        "cited_sources": (
            f"正文用 [S01] 形式实际引用至少 {profile.min_cited_sources} 个来源。"
        ),
        "cited_primary_sources": (
            "至少引用 "
            f"{profile.min_cited_primary_sources} 个官方文档、论文、标准或代码仓库。"
        ),
        "source_domains": (
            "扩大正文实际引用面，至少覆盖 "
            f"{profile.min_source_domains} 个独立域名。"
        ),
        "fact_check_manifest": (
            f"逐条核验至少 {profile.min_fact_checked_claims} 个关键主张，"
            "并清除仍在正文中的未决项。"
        ),
        "evidence_manifest": "完成至少一次可复现的实测/数据核对，记录命令或方法及产物。",
        "business_bridge": (
            "补齐 business-bridge.json：信号、技术变化、受影响流程、业务决策、"
            "验证方法、成功指标和不适用条件缺一不可。"
        ),
        "evidence_section": "增加“实测/复现/验证”章节，并报告方法、结果和误差。",
        "limitations_section": "增加“局限/风险/失效条件”章节。",
        "references_section": "增加参考来源章节，使 [Sxx] 能回查。",
        "visual": (
            f"加入至少 {profile.min_visuals} 张信息密度高的表格、图或 Mermaid 图。"
        ),
        "no_unresolved_markers": "清除 TODO、待核验、来源待补等占位符。",
    }
    recommendations = [
        recommendation_map[gate.name] for gate in gates if not gate.passed
    ]
    if score < profile.min_score:
        recommendations.append(
            f"当前机器可审计分 {score}/100，需达到 {profile.min_score}/100；"
            "该分数不替代人工对洞察、原创性和文风的判断。"
        )
    if formulaic_style_count > 8:
        recommendations.append(
            "减少“首先/其次/最后”“不是……而是……”等模板化连接，"
            "用事实关系和自然转折组织段落。"
        )

    return QualityReport(
        profile=profile.name,
        passed=passed,
        score=score,
        score_out_of_ten=round(score / 10, 1),
        metrics=metrics,
        category_scores=category_scores,
        gates=gates,
        recommendations=recommendations,
    )


def _format_report(report: QualityReport) -> str:
    status = "PASS" if report.passed else "FAIL"
    lines = [
        (
            f"{status} · {report.score}/100 ({report.score_out_of_ten}/10) · "
            f"profile={report.profile}"
        ),
        "",
        "Hard gates:",
    ]
    for gate in report.gates:
        marker = "✓" if gate.passed else "✗"
        lines.append(
            f"  {marker} {gate.name}: actual={gate.actual!r}, expected={gate.expected}"
        )
    lines.extend(("", "Category scores:"))
    for name, score in report.category_scores.items():
        lines.append(f"  - {name}: {score}")
    if report.recommendations:
        lines.extend(("", "Next fixes:"))
        lines.extend(f"  - {item}" for item in report.recommendations)
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit an evidence-backed long-form Markdown article."
    )
    parser.add_argument("article", type=Path, help="Path to article.md")
    parser.add_argument("--sources", type=Path, help="Path to source-index.json")
    parser.add_argument("--fact-check", type=Path, help="Path to fact-check.json")
    parser.add_argument("--evidence", type=Path, help="Path to evidence.json")
    parser.add_argument(
        "--business-bridge",
        type=Path,
        help="Path to business-bridge.json",
    )
    parser.add_argument(
        "--profile", choices=sorted(PROFILES), default=DEFAULT_PROFILE.name
    )
    parser.add_argument("--json-out", type=Path, help="Write full report as JSON")
    parser.add_argument(
        "--format", choices=("text", "json"), default="text", dest="output_format"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        article = args.article.read_text(encoding="utf-8")
        report = evaluate_article(
            article,
            source_payload=_read_json(args.sources, "sources"),
            fact_payload=_read_json(args.fact_check, "fact-check"),
            evidence_payload=_read_json(args.evidence, "evidence"),
            business_bridge_payload=_read_json(
                args.business_bridge, "business-bridge"
            ),
            evidence_root=args.evidence.parent if args.evidence else None,
            profile=PROFILES[args.profile],
        )
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"content-quality: {exc}", file=sys.stderr)
        return 2

    payload = json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(payload + "\n", encoding="utf-8")
    print(payload if args.output_format == "json" else _format_report(report))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
