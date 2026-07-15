"""Unit tests for prax/core/feature_list.py."""
from __future__ import annotations

import json

from prax.core.feature_list import FeatureList


def _write(tmp_path, data):
    d = tmp_path / ".prax"
    d.mkdir(parents=True, exist_ok=True)
    (d / "feature_list.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


_SAMPLE = {
    "features": [
        {"id": "f1", "title": "登录页", "acceptance": "pytest tests/test_login.py", "priority": 1, "status": "done"},
        {"id": "f2", "title": "会话列表", "acceptance": "npm test", "priority": 2, "status": "pending"},
        {"id": "f3", "title": "设置页", "priority": 5, "status": "pending"},
    ]
}


class TestFeatureList:
    def test_load_parses_features(self, tmp_path):
        _write(tmp_path, _SAMPLE)
        items = FeatureList(str(tmp_path)).load()
        assert [f.id for f in items] == ["f1", "f2", "f3"]
        assert items[0].done is True
        assert items[1].acceptance == "npm test"

    def test_next_pending_is_highest_priority_not_done(self, tmp_path):
        _write(tmp_path, _SAMPLE)
        nxt = FeatureList(str(tmp_path)).next_pending()
        assert nxt is not None and nxt.id == "f2"  # f1 done, f2 prio 2 < f3 prio 5

    def test_mark_done_persists(self, tmp_path):
        _write(tmp_path, _SAMPLE)
        fl = FeatureList(str(tmp_path))
        assert fl.mark_done("f2") is True
        # re-read from disk: f2 now done → next pending is f3
        assert fl.next_pending().id == "f3"
        assert fl.mark_done("f2") is False  # already done → no change

    def test_format_for_prompt_marks_next_and_done(self, tmp_path):
        _write(tmp_path, _SAMPLE)
        out = FeatureList(str(tmp_path)).format_for_prompt()
        assert "[x] f1 登录页" in out
        assert "← 本轮推进" in out
        assert "勿删测试" in out

    def test_missing_file_is_empty(self, tmp_path):
        fl = FeatureList(str(tmp_path))
        assert fl.load() == []
        assert fl.next_pending() is None
        assert fl.format_for_prompt() == ""
        assert fl.mark_done("f1") is False

    def test_malformed_file_is_empty(self, tmp_path):
        d = tmp_path / ".prax"
        d.mkdir(parents=True, exist_ok=True)
        (d / "feature_list.json").write_text("{ not json", encoding="utf-8")
        assert FeatureList(str(tmp_path)).load() == []

    def test_items_without_id_are_skipped(self, tmp_path):
        _write(tmp_path, {"features": [{"title": "no id"}, {"id": "ok"}]})
        assert [f.id for f in FeatureList(str(tmp_path)).load()] == ["ok"]

    def test_risk_category_roundtrips_into_feature_and_prompt(self, tmp_path):
        _write(tmp_path, {"features": [
            {"id": "f1", "title": "改价格", "risk_category": "money"},
        ]})
        fl = FeatureList(str(tmp_path))
        assert fl.load()[0].risk_category == "money"
        assert "风险：money" in fl.format_for_prompt()

    def test_missing_risk_category_is_unspecified_and_will_fail_closed(self, tmp_path):
        _write(tmp_path, {"features": [{"id": "f1"}]})
        assert FeatureList(str(tmp_path)).load()[0].risk_category == "unspecified"
