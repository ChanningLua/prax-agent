"""Tests for prax.core.yolo_classifier."""

from prax.core.yolo_classifier import RiskLevel, YoloClassifier


class TestClassifyBash:
    def setup_method(self):
        self.classifier = YoloClassifier(use_llm_fallback=False)

    def test_high_risk_rm_rf(self):
        d = self.classifier.classify_bash("rm -rf /")
        assert d.risk == RiskLevel.HIGH
        assert d.allow is False

    def test_high_risk_git_push_force(self):
        d = self.classifier.classify_bash("git push origin main --force")
        assert d.risk == RiskLevel.HIGH
        assert d.allow is False

    def test_high_risk_drop_table(self):
        d = self.classifier.classify_bash("DROP TABLE users;")
        assert d.risk == RiskLevel.HIGH
        assert d.allow is False

    def test_high_risk_git_reset_hard(self):
        d = self.classifier.classify_bash("git reset --hard HEAD~3")
        assert d.risk == RiskLevel.HIGH
        assert d.allow is False

    def test_low_risk_ls(self):
        d = self.classifier.classify_bash("ls -la")
        assert d.risk == RiskLevel.LOW
        assert d.allow is True

    def test_low_risk_git_status(self):
        d = self.classifier.classify_bash("git status")
        assert d.risk == RiskLevel.LOW
        assert d.allow is True

    def test_low_risk_cat(self):
        d = self.classifier.classify_bash("cat README.md")
        assert d.risk == RiskLevel.LOW
        assert d.allow is True

    def test_medium_risk_npm_install(self):
        d = self.classifier.classify_bash("npm install express")
        assert d.risk == RiskLevel.MEDIUM

    def test_medium_risk_python_script(self):
        d = self.classifier.classify_bash("python script.py")
        assert d.risk == RiskLevel.MEDIUM

    def test_empty_string(self):
        d = self.classifier.classify_bash("")
        assert d.risk == RiskLevel.MEDIUM
