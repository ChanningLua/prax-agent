"""Unit tests for prax/core/feature_driver.py.

run_feature is injected, so the driver's selection/advance/mark-done/stop logic
is exercised against a real FeatureList (temp dir) without a real claude -p.
"""
from __future__ import annotations

import json

from prax.core.feature_driver import run_features
from prax.core.feature_list import FeatureList
from prax.core.orchestrator_loop import OrchestrationOutcome


def _write(tmp_path, features):
    d = tmp_path / ".prax"
    d.mkdir(parents=True, exist_ok=True)
    (d / "feature_list.json").write_text(
        json.dumps({"features": features}, ensure_ascii=False), encoding="utf-8"
    )


def _verified():
    return OrchestrationOutcome(stop_reason="verified", iterations=1, verified=True)


def _blocked(reason="max_iterations"):
    return OrchestrationOutcome(stop_reason=reason, iterations=3, verified=False)


class TestFeatureDriver:
    def test_all_features_done_in_priority_order(self, tmp_path):
        _write(tmp_path, [
            {"id": "f1", "priority": 2, "status": "pending"},
            {"id": "f2", "priority": 1, "status": "pending"},
        ])
        fl = FeatureList(str(tmp_path))
        seen = []
        report = run_features(fl, lambda f: seen.append(f.id) or _verified())
        assert report.stop_reason == "all_features_done"
        assert report.completed == ["f2", "f1"]          # priority order
        assert seen == ["f2", "f1"]
        assert fl.next_pending() is None                 # persisted done

    def test_skips_already_done(self, tmp_path):
        _write(tmp_path, [
            {"id": "f1", "priority": 1, "status": "done"},
            {"id": "f2", "priority": 2, "status": "pending"},
        ])
        fl = FeatureList(str(tmp_path))
        report = run_features(fl, lambda f: _verified())
        assert report.completed == ["f2"]                # f1 already done, skipped

    def test_stops_at_first_unverified_feature(self, tmp_path):
        _write(tmp_path, [
            {"id": "f1", "priority": 1, "status": "pending"},
            {"id": "f2", "priority": 2, "status": "pending"},
        ])
        fl = FeatureList(str(tmp_path))

        def run(f):
            return _verified() if f.id == "f1" else _blocked("stuck_no_progress")

        report = run_features(fl, run)
        assert report.stop_reason == "feature_blocked"
        assert report.blocked_id == "f2"
        assert report.completed == ["f1"]
        assert fl.next_pending().id == "f2"              # f2 NOT marked done

    def test_max_features_cap(self, tmp_path):
        _write(tmp_path, [
            {"id": "f1", "priority": 1, "status": "pending"},
            {"id": "f2", "priority": 2, "status": "pending"},
        ])
        fl = FeatureList(str(tmp_path))
        report = run_features(fl, lambda f: _verified(), max_features=1)
        assert report.stop_reason == "max_features"
        assert report.completed == ["f1"]
        assert fl.next_pending().id == "f2"              # f2 left for next window

    def test_single_feature_with_cap_is_all_done_not_max(self, tmp_path):
        # regression: the ONLY feature finishing exactly as max_features is hit
        # must report all_features_done (verified), NOT a misleading max_features.
        _write(tmp_path, [{"id": "f1", "priority": 1, "status": "pending"}])
        fl = FeatureList(str(tmp_path))
        report = run_features(fl, lambda f: _verified(), max_features=1)
        assert report.stop_reason == "all_features_done"
        assert report.completed == ["f1"]
        assert fl.next_pending() is None

    def test_empty_list_is_all_done(self, tmp_path):
        _write(tmp_path, [])
        report = run_features(FeatureList(str(tmp_path)), lambda f: _verified())
        assert report.stop_reason == "all_features_done"
        assert report.completed == []
