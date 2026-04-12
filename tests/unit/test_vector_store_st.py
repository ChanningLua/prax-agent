"""Tests for VectorStore embedding strategy selection."""

import os
from unittest.mock import patch, MagicMock

import pytest

from prax.core.memory.vector_store import (
    _ngram_embedding,
    _st_embedding,
    _get_st_model,
    _use_sentence_transformer,
    get_embedding_info,
    _embed_texts,
    _DIM,
)


class TestNgramEmbedding:
    def test_returns_correct_dim(self):
        vec = _ngram_embedding("hello world")
        assert len(vec) == _DIM

    def test_unit_length(self):
        import math
        vec = _ngram_embedding("test sentence for embedding")
        norm = math.sqrt(sum(x * x for x in vec))
        assert abs(norm - 1.0) < 1e-6

    def test_empty_string(self):
        vec = _ngram_embedding("")
        assert len(vec) == _DIM

    def test_chinese_text(self):
        vec = _ngram_embedding("你好世界")
        assert len(vec) == _DIM

    def test_similar_texts_closer(self):
        """Similar texts should have higher cosine similarity than dissimilar ones."""
        v1 = _ngram_embedding("python programming language")
        v2 = _ngram_embedding("python coding language")
        v3 = _ngram_embedding("banana fruit yellow")

        # Cosine similarity (vectors are unit-length, so dot product = cosine)
        sim_12 = sum(a * b for a, b in zip(v1, v2))
        sim_13 = sum(a * b for a, b in zip(v1, v3))
        assert sim_12 > sim_13


class TestUseSentenceTransformer:
    def test_ngram_env_forces_ngram(self):
        with patch.dict(os.environ, {"PRAX_EMBEDDING": "ngram"}):
            assert _use_sentence_transformer() is False

    def test_default_returns_true(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove PRAX_EMBEDDING if set
            os.environ.pop("PRAX_EMBEDDING", None)
            assert _use_sentence_transformer() is True


class TestStEmbeddingFallback:
    def test_falls_back_to_ngram_when_st_unavailable(self):
        """When SentenceTransformer is not installed, _st_embedding falls back to ngram."""
        # _st_embedding checks _get_st_model() which returns None if unavailable
        # We mock _get_st_model to return None
        with patch("prax.core.memory.vector_store._get_st_model", return_value=None):
            result = _st_embedding(["hello world"])
            assert len(result) == 1
            assert len(result[0]) == _DIM  # ngram dimension


class TestEmbedTexts:
    def test_ngram_mode(self):
        with patch("prax.core.memory.vector_store._use_sentence_transformer", return_value=False):
            result = _embed_texts(["test text"])
            assert len(result) == 1
            assert len(result[0]) == _DIM

    def test_st_mode_with_fallback(self):
        with patch("prax.core.memory.vector_store._use_sentence_transformer", return_value=True):
            with patch("prax.core.memory.vector_store._st_embedding") as mock_st:
                mock_st.return_value = [[0.1] * 384]
                result = _embed_texts(["test text"])
                mock_st.assert_called_once_with(["test text"])
                assert len(result) == 1


class TestGetEmbeddingInfo:
    def test_ngram_info(self):
        with patch("prax.core.memory.vector_store._use_sentence_transformer", return_value=False):
            info = get_embedding_info()
            assert info["strategy"] == "ngram"
            assert info["dimensions"] == _DIM

    def test_st_info_when_model_available(self):
        mock_model = MagicMock()
        with patch("prax.core.memory.vector_store._use_sentence_transformer", return_value=True):
            with patch("prax.core.memory.vector_store._get_st_model", return_value=mock_model):
                info = get_embedding_info()
                assert info["strategy"] == "sentence_transformer"
                assert info["dimensions"] == 384

    def test_st_info_when_model_unavailable(self):
        with patch("prax.core.memory.vector_store._use_sentence_transformer", return_value=True):
            with patch("prax.core.memory.vector_store._get_st_model", return_value=None):
                info = get_embedding_info()
                assert info["strategy"] == "ngram"
