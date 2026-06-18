"""Unit tests for prax.core.config_files.load_permission_mode."""
from __future__ import annotations

import pytest

from prax.core.config_files import load_permission_mode
from prax.core.permissions import PermissionMode


@pytest.fixture
def project(tmp_path, monkeypatch):
    # isolate user home so only the project config under test is read
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    proj = tmp_path / "proj"
    proj.mkdir()
    return proj


def _write(proj, text):
    d = proj / ".prax"
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.yaml").write_text(text, encoding="utf-8")


def test_returns_none_when_unset(project):
    assert load_permission_mode(str(project)) is None


def test_reads_danger_full_access(project):
    _write(project, "permission_mode: danger-full-access\n")
    assert load_permission_mode(str(project)) == PermissionMode.DANGER_FULL_ACCESS


def test_accepts_dangerous_alias(project):
    _write(project, "permission_mode: dangerous\n")
    assert load_permission_mode(str(project)) == PermissionMode.DANGER_FULL_ACCESS


def test_read_only(project):
    _write(project, "permission_mode: read-only\n")
    assert load_permission_mode(str(project)) == PermissionMode.READ_ONLY


def test_invalid_value_returns_none(project):
    _write(project, "permission_mode: bogus\n")
    assert load_permission_mode(str(project)) is None
