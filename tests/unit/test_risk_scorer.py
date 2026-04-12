"""Unit tests for prax/core/risk_scorer.py."""
from __future__ import annotations

import pytest

from prax.core.risk_scorer import (
    RiskScore,
    RiskScorer,
    _TOOL_RISK,
    _DESTRUCTIVE_CMDS,
    _PUBLISH_CMDS,
    _REVERSIBLE_CMDS,
)


# ---------------------------------------------------------------------------
# RiskScore.level
# ---------------------------------------------------------------------------

def test_level_low_boundary():
    s = RiskScore(tool_risk=1, file_sensitivity=1, impact_scope=1, reversibility=1)
    assert s.total == 4
    assert s.level == "LOW"


def test_level_low_at_8():
    s = RiskScore(tool_risk=2, file_sensitivity=2, impact_scope=2, reversibility=2)
    assert s.total == 8
    assert s.level == "LOW"


def test_level_medium_at_9():
    s = RiskScore(tool_risk=3, file_sensitivity=2, impact_scope=2, reversibility=2)
    assert s.total == 9
    assert s.level == "MEDIUM"


def test_level_medium_at_14():
    s = RiskScore(tool_risk=4, file_sensitivity=4, impact_scope=3, reversibility=3)
    assert s.total == 14
    assert s.level == "MEDIUM"


def test_level_high_at_15():
    s = RiskScore(tool_risk=4, file_sensitivity=4, impact_scope=4, reversibility=3)
    assert s.total == 15
    assert s.level == "HIGH"


def test_level_high_at_20():
    s = RiskScore(tool_risk=5, file_sensitivity=5, impact_scope=5, reversibility=5)
    assert s.total == 20
    assert s.level == "HIGH"


# ---------------------------------------------------------------------------
# RiskScore.summary()
# ---------------------------------------------------------------------------

def test_summary_format():
    s = RiskScore(tool_risk=2, file_sensitivity=3, impact_scope=1, reversibility=2)
    result = s.summary()
    assert "8/20" in result
    assert "LOW" in result
    assert "tool=2" in result
    assert "file=3" in result
    assert "scope=1" in result
    assert "reversibility=2" in result


def test_summary_high_level():
    s = RiskScore(tool_risk=5, file_sensitivity=5, impact_scope=5, reversibility=5)
    assert "HIGH" in s.summary()
    assert "20/20" in s.summary()


# ---------------------------------------------------------------------------
# _TOOL_RISK dict entries
# ---------------------------------------------------------------------------

def test_tool_risk_read_only_tools():
    scorer = RiskScorer()
    for tool in ("HashlineRead", "Read", "Glob", "Grep", "AstGrepSearch", "WebSearch", "WebCrawler"):
        assert scorer._tool_risk(tool) == 1, f"{tool} should be 1"


def test_tool_risk_write_edit_tools():
    scorer = RiskScorer()
    for tool in ("Write", "Edit", "HashlineEdit", "AstGrepReplace"):
        assert scorer._tool_risk(tool) == 3, f"{tool} should be 3"


def test_tool_risk_todo_write():
    assert RiskScorer()._tool_risk("TodoWrite") == 2


def test_tool_risk_shell_tools():
    scorer = RiskScorer()
    assert scorer._tool_risk("TmuxBash") == 5
    assert scorer._tool_risk("Bash") == 5


def test_tool_risk_task_tools():
    scorer = RiskScorer()
    assert scorer._tool_risk("Task") == 4
    assert scorer._tool_risk("StartTask") == 4
    assert scorer._tool_risk("CheckTask") == 1
    assert scorer._tool_risk("UpdateTask") == 2
    assert scorer._tool_risk("CancelTask") == 3
    assert scorer._tool_risk("ListTasks") == 1


def test_tool_risk_unknown_defaults_to_3():
    scorer = RiskScorer()
    assert scorer._tool_risk("UnknownTool") == 3
    assert scorer._tool_risk("") == 3
    assert scorer._tool_risk("SomeFutureTool") == 3


# ---------------------------------------------------------------------------
# _file_sensitivity
# ---------------------------------------------------------------------------

def test_file_sensitivity_env_file():
    scorer = RiskScorer()
    assert scorer._file_sensitivity({"file_path": "/project/.env"}) == 5


def test_file_sensitivity_env_with_suffix():
    scorer = RiskScorer()
    assert scorer._file_sensitivity({"file_path": "/project/.env.production"}) == 5


def test_file_sensitivity_credentials():
    scorer = RiskScorer()
    assert scorer._file_sensitivity({"file_path": "/home/user/credentials.json"}) == 5


def test_file_sensitivity_key_file():
    scorer = RiskScorer()
    assert scorer._file_sensitivity({"file_path": "/home/user/id_rsa.key"}) == 5


def test_file_sensitivity_pem_file():
    scorer = RiskScorer()
    assert scorer._file_sensitivity({"file_path": "/certs/server.pem"}) == 5


def test_file_sensitivity_github_workflows():
    scorer = RiskScorer()
    assert scorer._file_sensitivity({"file_path": "/project/.github/workflows/ci.yml"}) == 4


def test_file_sensitivity_dockerfile():
    scorer = RiskScorer()
    assert scorer._file_sensitivity({"file_path": "/project/Dockerfile"}) == 4


def test_file_sensitivity_pyproject_toml():
    scorer = RiskScorer()
    assert scorer._file_sensitivity({"file_path": "/project/pyproject.toml"}) == 4


def test_file_sensitivity_package_json():
    scorer = RiskScorer()
    assert scorer._file_sensitivity({"file_path": "/project/package.json"}) == 4


def test_file_sensitivity_src_dir():
    scorer = RiskScorer()
    assert scorer._file_sensitivity({"file_path": "/project/src/main.py"}) == 2


def test_file_sensitivity_tests_dir():
    scorer = RiskScorer()
    assert scorer._file_sensitivity({"file_path": "/project/tests/test_foo.py"}) == 1


def test_file_sensitivity_no_path_returns_1():
    scorer = RiskScorer()
    assert scorer._file_sensitivity({}) == 1
    assert scorer._file_sensitivity({"file_path": ""}) == 1
    assert scorer._file_sensitivity({"file_path": None}) == 1


def test_file_sensitivity_unknown_path_returns_2():
    scorer = RiskScorer()
    assert scorer._file_sensitivity({"file_path": "/project/somefile.txt"}) == 2


def test_file_sensitivity_uses_path_key():
    scorer = RiskScorer()
    assert scorer._file_sensitivity({"path": "/project/.env"}) == 5


# ---------------------------------------------------------------------------
# _DESTRUCTIVE_CMDS patterns
# ---------------------------------------------------------------------------

def test_destructive_cmd_rm_rf():
    scorer = RiskScorer()
    assert scorer._impact_scope("Bash", {"command": "rm -rf /tmp/foo"}) == 5


def test_destructive_cmd_git_reset_hard():
    scorer = RiskScorer()
    assert scorer._impact_scope("Bash", {"command": "git reset --hard HEAD"}) == 5


def test_destructive_cmd_git_push_force():
    scorer = RiskScorer()
    assert scorer._impact_scope("Bash", {"command": "git push --force origin main"}) == 5


def test_destructive_cmd_drop_table():
    scorer = RiskScorer()
    assert scorer._impact_scope("Bash", {"command": "DROP TABLE users"}) == 5


def test_destructive_cmd_truncate():
    scorer = RiskScorer()
    assert scorer._impact_scope("Bash", {"command": "truncate -s 0 file.txt"}) == 5


def test_destructive_cmd_dd():
    scorer = RiskScorer()
    assert scorer._impact_scope("Bash", {"command": "dd if=/dev/zero of=/dev/sda"}) == 5


def test_destructive_cmd_mkfs():
    scorer = RiskScorer()
    assert scorer._impact_scope("Bash", {"command": "mkfs.ext4 /dev/sdb"}) == 5


# ---------------------------------------------------------------------------
# _PUBLISH_CMDS patterns
# ---------------------------------------------------------------------------

def test_publish_cmd_git_push():
    scorer = RiskScorer()
    assert scorer._impact_scope("Bash", {"command": "git push origin main"}) == 4


def test_publish_cmd_npm_publish():
    scorer = RiskScorer()
    assert scorer._impact_scope("Bash", {"command": "npm publish"}) == 4


def test_publish_cmd_pip_upload():
    scorer = RiskScorer()
    assert scorer._impact_scope("Bash", {"command": "pip upload dist/*"}) == 4


def test_publish_cmd_twine_upload():
    scorer = RiskScorer()
    assert scorer._impact_scope("Bash", {"command": "twine upload dist/*"}) == 4


def test_publish_cmd_docker_push():
    scorer = RiskScorer()
    assert scorer._impact_scope("Bash", {"command": "docker push myimage:latest"}) == 4


# ---------------------------------------------------------------------------
# _REVERSIBLE_CMDS patterns
# ---------------------------------------------------------------------------

def test_reversible_cmd_git_commit():
    scorer = RiskScorer()
    assert scorer._impact_scope("Bash", {"command": "git commit -m 'fix'"}) == 3


def test_reversible_cmd_npm_install():
    scorer = RiskScorer()
    assert scorer._impact_scope("Bash", {"command": "npm install"}) == 3


def test_reversible_cmd_pip_install():
    scorer = RiskScorer()
    assert scorer._impact_scope("Bash", {"command": "pip install requests"}) == 3


def test_reversible_cmd_poetry_add():
    scorer = RiskScorer()
    assert scorer._impact_scope("Bash", {"command": "poetry add httpx"}) == 3


# ---------------------------------------------------------------------------
# _impact_scope
# ---------------------------------------------------------------------------

def test_impact_scope_bash_normal_cmd():
    scorer = RiskScorer()
    assert scorer._impact_scope("Bash", {"command": "ls -la"}) == 2


def test_impact_scope_tmuxbash_destructive():
    scorer = RiskScorer()
    assert scorer._impact_scope("TmuxBash", {"command": "rm -rf /tmp"}) == 5


def test_impact_scope_task():
    scorer = RiskScorer()
    assert scorer._impact_scope("Task", {}) == 3


def test_impact_scope_start_task():
    scorer = RiskScorer()
    assert scorer._impact_scope("StartTask", {}) == 3


def test_impact_scope_write():
    scorer = RiskScorer()
    assert scorer._impact_scope("Write", {"file_path": "/foo.py"}) == 1


def test_impact_scope_edit():
    scorer = RiskScorer()
    assert scorer._impact_scope("Edit", {"file_path": "/foo.py"}) == 1


def test_impact_scope_read_default():
    scorer = RiskScorer()
    assert scorer._impact_scope("Read", {}) == 1


# ---------------------------------------------------------------------------
# _reversibility
# ---------------------------------------------------------------------------

def test_reversibility_bash_destructive():
    scorer = RiskScorer()
    assert scorer._reversibility("Bash", {"command": "rm -rf /"}) == 5


def test_reversibility_bash_publish():
    scorer = RiskScorer()
    assert scorer._reversibility("Bash", {"command": "git push origin main"}) == 4


def test_reversibility_bash_reversible():
    scorer = RiskScorer()
    assert scorer._reversibility("Bash", {"command": "git commit -m 'wip'"}) == 2


def test_reversibility_bash_normal():
    scorer = RiskScorer()
    assert scorer._reversibility("Bash", {"command": "echo hello"}) == 1


def test_reversibility_tmuxbash_destructive():
    scorer = RiskScorer()
    assert scorer._reversibility("TmuxBash", {"command": "DROP TABLE sessions"}) == 5


def test_reversibility_write():
    scorer = RiskScorer()
    assert scorer._reversibility("Write", {"file_path": "/foo.py"}) == 1


def test_reversibility_edit():
    scorer = RiskScorer()
    assert scorer._reversibility("Edit", {"file_path": "/foo.py"}) == 1


def test_reversibility_default_tool():
    scorer = RiskScorer()
    assert scorer._reversibility("Read", {}) == 2
    assert scorer._reversibility("Glob", {}) == 2


# ---------------------------------------------------------------------------
# End-to-end RiskScorer.score()
# ---------------------------------------------------------------------------

def test_score_read_only_low_risk():
    scorer = RiskScorer()
    result = scorer.score("Read", {"file_path": "/project/README.md"})
    assert result.tool_risk == 1
    assert result.file_sensitivity == 1
    assert result.level == "LOW"


def test_score_write_to_env_file():
    scorer = RiskScorer()
    result = scorer.score("Write", {"file_path": "/project/.env"})
    assert result.tool_risk == 3
    assert result.file_sensitivity == 5
    assert result.impact_scope == 1
    assert result.reversibility == 1
    assert result.total == 10
    assert result.level == "MEDIUM"


def test_score_bash_destructive_high_risk():
    scorer = RiskScorer()
    result = scorer.score("Bash", {"command": "rm -rf /important"})
    assert result.tool_risk == 5
    assert result.impact_scope == 5
    assert result.reversibility == 5
    assert result.level == "HIGH"


def test_score_bash_git_push_is_medium():
    scorer = RiskScorer()
    result = scorer.score("Bash", {"command": "git push origin main"})
    assert result.tool_risk == 5
    assert result.impact_scope == 4
    assert result.reversibility == 4
    # total = 5+1+4+4 = 14 — exactly at the MEDIUM ceiling (≤14)
    assert result.total == 14
    assert result.level == "MEDIUM"


def test_score_task_delegation():
    scorer = RiskScorer()
    result = scorer.score("Task", {})
    assert result.tool_risk == 4
    assert result.impact_scope == 3
    assert result.reversibility == 2
