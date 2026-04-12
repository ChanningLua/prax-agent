"""Tests for AAAK Dialect — compact symbolic encoding for KG triples."""

import pytest

from prax.core.memory.knowledge_graph import KnowledgeGraph
from prax.core.memory.dialect import Dialect, _make_code


@pytest.fixture
def kg(tmp_path):
    kg = KnowledgeGraph(str(tmp_path))
    kg.add_triple("user", "prefers", "Chinese language", confidence=0.95)
    kg.add_triple("project", "uses", "SQLite", confidence=0.9)
    kg.add_triple("project", "uses", "Python", confidence=0.9)
    kg.add_triple("Alice", "works_on", "Harness project", confidence=1.0)
    return kg


class TestMakeCode:
    def test_short_word(self):
        assert _make_code("user") == "USER"

    def test_single_long_word(self):
        code = _make_code("Python")
        assert len(code) <= 8
        assert code.isupper()

    def test_multi_word(self):
        code = _make_code("Chinese language")
        assert "_" in code or len(code) <= 8
        assert code.isupper()

    def test_empty(self):
        code = _make_code("")
        assert len(code) <= 8


class TestDialectFromKG:
    def test_builds_codebook(self, kg):
        dialect = Dialect.from_kg(kg)
        codebook = dialect.build_codebook()
        assert "CODES:" in codebook
        assert "=" in codebook

    def test_no_code_collisions(self, kg):
        dialect = Dialect.from_kg(kg)
        codes = list(dialect._codes.values())
        # All codes should be unique
        assert len(codes) == len(set(codes))


class TestCompressTriple:
    def test_basic_compression(self):
        dialect = Dialect(entity_codes={"user": "USR", "python": "PYTH"})
        result = dialect.compress_triple({
            "subject": "user",
            "predicate": "uses",
            "object": "Python",
        })
        assert "USR" in result
        assert "uses" in result
        assert "PYTH" in result

    def test_includes_confidence(self):
        dialect = Dialect(entity_codes={"user": "USR", "vim": "VIM"})
        result = dialect.compress_triple({
            "subject": "user",
            "predicate": "prefers",
            "object": "vim",
            "confidence": 0.8,
        })
        assert "0.8" in result

    def test_includes_valid_from(self):
        dialect = Dialect(entity_codes={"user": "USR", "rust": "RST"})
        result = dialect.compress_triple({
            "subject": "user",
            "predicate": "learns",
            "object": "rust",
            "valid_from": "2026-01-01",
        })
        assert "2026-01-01" in result


class TestCompressTriples:
    def test_multi_line_output(self, kg):
        dialect = Dialect.from_kg(kg)
        triples = kg.get_top_triples(limit=10, min_confidence=0.5)
        compressed = dialect.compress_triples(triples)
        lines = compressed.strip().split("\n")
        assert len(lines) >= 3

    def test_empty_input(self):
        dialect = Dialect()
        assert dialect.compress_triples([]) == ""


class TestCompressionRatio:
    def test_compression_achieves_reduction(self, kg):
        dialect = Dialect.from_kg(kg)
        triples = kg.get_top_triples(limit=10, min_confidence=0.5)

        # Build "original" verbose format
        original_lines = []
        for t in triples:
            original_lines.append(
                f"- {t['subject']} → {t['predicate']} → {t['object']}"
            )
        original = "\n".join(original_lines)

        compressed = dialect.compress_triples(triples)
        stats = dialect.compression_stats(original, compressed)

        # Compressed should be shorter
        assert stats["compressed_chars"] < stats["original_chars"]
        assert stats["ratio"] >= 1.0


class TestCodebookRoundTrip:
    def test_save_and_load(self, tmp_path, kg):
        dialect = Dialect.from_kg(kg)
        codebook_path = tmp_path / "codebook.json"
        dialect.save_codebook(codebook_path)

        loaded = Dialect.from_codebook(codebook_path)
        # Should encode the same entities to the same codes
        assert dialect.encode_entity("user") == loaded.encode_entity("user")
        assert dialect.encode_entity("project") == loaded.encode_entity("project")


class TestBuildCodebook:
    def test_format(self):
        dialect = Dialect(entity_codes={"user": "USR", "python": "PYTH"})
        codebook = dialect.build_codebook()
        assert codebook.startswith("CODES:")
        assert "USR=user" in codebook
        assert "PYTH=python" in codebook

    def test_empty(self):
        dialect = Dialect()
        assert dialect.build_codebook() == ""


class TestCompressForL1:
    def test_includes_codebook_and_triples(self, kg):
        dialect = Dialect.from_kg(kg)
        triples = kg.get_top_triples(limit=5, min_confidence=0.5)
        result = dialect.compress_for_l1(triples)
        assert "CODES:" in result
        assert "|" in result

    def test_empty_triples(self):
        dialect = Dialect()
        assert dialect.compress_for_l1([]) == ""
