from __future__ import annotations

import json

from prax.content_quality import (
    SELECTED_ANALYSIS_PROFILE,
    SELECTED_FRAMEWORK_PROFILE,
    SELECTED_HANDS_ON_PROFILE,
    SELECTED_PROFILE,
    SELECTED_SHARP_PROFILE,
    evaluate_article,
    main,
)


def _article(*, long_enough: bool = True, unresolved: bool = False) -> str:
    headings = [
        "为什么这个问题现在重要",
        "背景与关键矛盾",
        "系统的工作原理",
        "核心机制拆解",
        "实现步骤",
        "真实场景案例",
        "实测与复现",
        "结果如何解释",
        "失败模式与反例",
        "局限与风险",
        "行动建议",
        "结论",
        "参考来源",
    ]
    citations = " ".join(f"[S{i:02d}]" for i in range(1, 11))
    body = "这是经过来源交叉验证的技术分析，讨论事实、观察、推断、误差和代价。"
    body += "风险失败反例误差代价。" * 2
    repeat = 12 if long_enough else 2
    sections = "\n\n".join(
        f"## {heading}\n\n{body * repeat}\n\n{citations}"
        for heading in headings
    )
    marker = "\n\n待核验" if unresolved else ""
    return (
        "# 一篇证据驱动的深度文章\n\n"
        "| 方案 | 收益 | 风险 |\n"
        "|---|---|---|\n"
        "| A | 可验证 | 有边界 |\n\n"
        f"{sections}{marker}\n"
    )


def _sources() -> dict:
    source_types = [
        "official",
        "documentation",
        "paper",
        "repository",
        "official",
        "paper",
        "media",
        "media",
        "community",
        "media",
    ]
    sources = []
    for index in range(1, 21):
        sources.append(
            {
                "id": f"S{index:02d}",
                "title": f"Source {index}",
                "url": f"https://domain{index % 7}.example/item/{index}",
                "source_type": source_types[(index - 1) % len(source_types)],
            }
        )
    return {"sources": sources}


def _facts() -> dict:
    return {
        "claims": [
            {
                "id": f"C{index:02d}",
                "claim": f"Claim {index}",
                "source_ids": [f"S{index:02d}"],
                "status": "verified",
                "included_in_article": True,
            }
            for index in range(1, 9)
        ]
    }


def _evidence() -> dict:
    return {
        "runs": [
            {
                "name": "reproduction",
                "command": "tool --version",
                "result": "verified output",
                "limitations": "A single local run does not establish production performance.",
                "artifact": "evidence/run-01.txt",
                "status": "passed",
            }
        ]
    }


def _business_bridge() -> dict:
    return {
        "topic_id": "customer-search",
        "status": "ready-for-deep-research",
        "business_bridge": {
            "source_signal": "A customer project entered technical selection.",
            "technical_delta": (
                "Live retrieval adds freshness, citation, latency, and cost constraints "
                "to a static model answer."
            ),
            "affected_workflow": "Solution design and answer-quality acceptance.",
            "business_decision": "Build, buy, or combine search capabilities.",
            "validation_method": "Run a fixed query set against all three architectures.",
            "success_metric": "Meet the project's quality, latency, and cost thresholds.",
            "no_fit_conditions": "No live retrieval is needed for a static corpus.",
        },
    }


def test_selected_profile_passes_complete_evidence_package():
    report = evaluate_article(
        _article(),
        source_payload=_sources(),
        fact_payload=_facts(),
        evidence_payload=_evidence(),
        business_bridge_payload=_business_bridge(),
    )

    assert report.passed
    assert report.score >= SELECTED_PROFILE.min_score
    assert report.metrics["cited_source_count"] == 10
    assert report.metrics["cited_primary_source_count"] >= 4
    assert all(gate.passed for gate in report.gates)


def test_selected_sharp_profile_accepts_a_short_focused_article():
    sources = _sources()
    facts = _facts()
    headings = [
        "问题发生在哪里",
        "真实业务场景",
        "方案怎么落地",
        "一次可复现测试",
        "局限与风险",
        "参考来源",
    ]
    citations = " ".join(f"[S{i:02d}]" for i in range(1, 7))
    paragraph = (
        "本文围绕同一个业务决策展开，用官方证据、明确边界和可重复测试"
        "说明负责人该怎样放行。风险失败反例代价。"
    )
    article = (
        "# 一篇短而深的精选文章\n\n"
        "| 条件 | 决策 | 风险 |\n|---|---|---|\n| 隔离 | 放行 | 可回滚 |\n\n"
        + "\n\n".join(
            f"## {heading}\n\n{paragraph * 11}\n\n{citations}"
            for heading in headings
        )
    )

    report = evaluate_article(
        article,
        source_payload=sources,
        fact_payload=facts,
        evidence_payload=_evidence(),
        business_bridge_payload=_business_bridge(),
        profile=SELECTED_SHARP_PROFILE,
    )

    assert report.passed
    assert 3_500 <= report.metrics["content_chars"] <= 6_500
    assert 5 <= report.metrics["section_count"] <= 9


def test_tencent_selected_profiles_encode_distinct_editorial_routes():
    assert SELECTED_ANALYSIS_PROFILE.min_content_chars == 6_000
    assert SELECTED_ANALYSIS_PROFILE.min_visuals == 2
    assert SELECTED_FRAMEWORK_PROFILE.min_sections == 5
    assert SELECTED_FRAMEWORK_PROFILE.max_sections == 9
    assert SELECTED_HANDS_ON_PROFILE.min_evidence_runs == 2
    assert SELECTED_HANDS_ON_PROFILE.min_visuals == 4


def test_missing_business_bridge_blocks_an_otherwise_complete_package():
    report = evaluate_article(
        _article(),
        source_payload=_sources(),
        fact_payload=_facts(),
        evidence_payload=_evidence(),
    )

    assert not report.passed
    bridge_gate = next(gate for gate in report.gates if gate.name == "business_bridge")
    assert not bridge_gate.passed
    assert report.metrics["business_bridge_completeness"] == 0
    assert "technical_delta" in report.metrics["missing_business_bridge_fields"]


def test_long_text_alone_cannot_pass():
    report = evaluate_article(_article(), source_payload={"sources": []})

    assert not report.passed
    failed = {gate.name for gate in report.gates if not gate.passed}
    assert {
        "researched_sources",
        "cited_sources",
        "fact_check_manifest",
        "evidence_manifest",
    }.issubset(failed)


def test_formulaic_transitions_are_reported_as_editorial_signal():
    formulaic = "\n".join(
        [
            "首先，这是第一项。",
            "其次，这是第二项。",
            "最后，这不是普通变化而是系统变化。",
        ]
        * 4
    )
    report = evaluate_article(_article() + formulaic)

    assert report.metrics["formulaic_style_count"] > 8
    assert any("模板化连接" in item for item in report.recommendations)


def test_unresolved_marker_and_claim_fail_hard_gates():
    facts = _facts()
    facts["claims"][0]["status"] = "pending"
    report = evaluate_article(
        _article(unresolved=True),
        source_payload=_sources(),
        fact_payload=facts,
        evidence_payload=_evidence(),
    )

    assert not report.passed
    failed = {gate.name for gate in report.gates if not gate.passed}
    assert "fact_check_manifest" in failed
    assert "no_unresolved_markers" in failed


def test_duplicate_urls_and_reference_only_ids_do_not_count_as_citations():
    sources = _sources()
    sources["sources"][1]["url"] = sources["sources"][0]["url"]
    article = _article().replace(
        "## 参考来源",
        "## 参考来源\n\n[S11] [S12] [S13] [S14] [S15]",
    )
    report = evaluate_article(
        article,
        source_payload=sources,
        fact_payload=_facts(),
        evidence_payload=_evidence(),
    )

    assert report.metrics["rejected_or_duplicate_source_count"] == 1
    assert "S11" not in report.metrics["cited_source_ids"]


def test_uncited_research_domains_do_not_satisfy_domain_gate():
    sources = _sources()
    for index, source in enumerate(sources["sources"], start=1):
        if index <= 10:
            source["url"] = f"https://one.example/item/{index}"
    report = evaluate_article(
        _article(),
        source_payload=sources,
        fact_payload=_facts(),
        evidence_payload=_evidence(),
        business_bridge_payload=_business_bridge(),
    )

    assert report.metrics["researched_source_domain_count"] >= 6
    assert report.metrics["cited_source_domain_count"] == 1
    assert not next(
        gate for gate in report.gates if gate.name == "source_domains"
    ).passed


def test_evidence_without_limitations_is_not_accepted():
    evidence = _evidence()
    del evidence["runs"][0]["limitations"]
    report = evaluate_article(
        _article(),
        source_payload=_sources(),
        fact_payload=_facts(),
        evidence_payload=evidence,
        business_bridge_payload=_business_bridge(),
    )

    assert report.metrics["accepted_evidence_run_count"] == 0
    assert not next(
        gate for gate in report.gates if gate.name == "evidence_manifest"
    ).passed


def test_fact_claim_without_valid_source_link_is_unresolved():
    facts = _facts()
    facts["claims"][0]["source_ids"] = ["S99"]
    report = evaluate_article(
        _article(),
        source_payload=_sources(),
        fact_payload=facts,
        evidence_payload=_evidence(),
    )

    assert report.metrics["unresolved_claim_count"] == 1
    assert not next(
        gate for gate in report.gates if gate.name == "fact_check_manifest"
    ).passed


def test_cli_requires_evidence_artifact_to_exist(tmp_path):
    article = tmp_path / "article.md"
    sources = tmp_path / "sources.json"
    facts = tmp_path / "facts.json"
    evidence = tmp_path / "evidence.json"
    bridge = tmp_path / "business-bridge.json"
    article.write_text(_article(), encoding="utf-8")
    sources.write_text(json.dumps(_sources()), encoding="utf-8")
    facts.write_text(json.dumps(_facts()), encoding="utf-8")
    evidence.write_text(json.dumps(_evidence()), encoding="utf-8")
    bridge.write_text(json.dumps(_business_bridge()), encoding="utf-8")

    exit_code = main(
        [
            str(article),
            "--sources",
            str(sources),
            "--fact-check",
            str(facts),
            "--evidence",
            str(evidence),
            "--business-bridge",
            str(bridge),
            "--format",
            "json",
        ]
    )

    assert exit_code == 1


def test_cli_writes_json_report_and_returns_nonzero_on_failure(tmp_path):
    article = tmp_path / "article.md"
    sources = tmp_path / "sources.json"
    output = tmp_path / "quality-report.json"
    article.write_text(_article(long_enough=False), encoding="utf-8")
    sources.write_text(json.dumps(_sources()), encoding="utf-8")

    exit_code = main(
        [
            str(article),
            "--sources",
            str(sources),
            "--json-out",
            str(output),
            "--format",
            "json",
        ]
    )

    assert exit_code == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["passed"] is False
    assert any(
        gate["name"] == "content_chars" and not gate["passed"]
        for gate in payload["gates"]
    )
