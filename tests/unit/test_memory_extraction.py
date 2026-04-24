"""Tests for memory middleware (pure unit tests, no real I/O)."""
from __future__ import annotations

import asyncio
import json
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from prax.core.memory_middleware import (
    detect_correction_signal,
    MemoryExtractionMiddleware,
    CORRECTION_PATTERNS,
)
from prax.core.middleware import RuntimeState
from prax.core.llm_client import LLMResponse


# ── detect_correction_signal ─────────────────────────────

def test_detect_correction_english_wrong():
    msgs = [{"role": "user", "content": "that's wrong"}]
    assert detect_correction_signal(msgs) is True


def test_detect_correction_english_wrong_alt():
    msgs = [{"role": "user", "content": "that is wrong actually"}]
    assert detect_correction_signal(msgs) is True


def test_detect_correction_english_misunderstood():
    msgs = [{"role": "user", "content": "you misunderstood the problem"}]
    assert detect_correction_signal(msgs) is True


def test_detect_correction_try_again():
    msgs = [{"role": "user", "content": "please try again"}]
    assert detect_correction_signal(msgs) is True


def test_detect_correction_redo():
    msgs = [{"role": "user", "content": "please redo this"}]
    assert detect_correction_signal(msgs) is True


def test_detect_correction_chinese_budui():
    msgs = [{"role": "user", "content": "不对，重新做"}]
    assert detect_correction_signal(msgs) is True


def test_detect_correction_chinese_lijiecuole():
    msgs = [{"role": "user", "content": "你理解错了"}]
    assert detect_correction_signal(msgs) is True


def test_detect_correction_chinese_lijiecuole_alt():
    msgs = [{"role": "user", "content": "你理解有误，应该这样"}]
    assert detect_correction_signal(msgs) is True


def test_detect_correction_chinese_chongshi():
    msgs = [{"role": "user", "content": "请重试一下"}]
    assert detect_correction_signal(msgs) is True


def test_detect_correction_chinese_chonglai():
    msgs = [{"role": "user", "content": "重新来过"}]
    assert detect_correction_signal(msgs) is True


def test_detect_correction_chinese_huanyizhong():
    msgs = [{"role": "user", "content": "换一种方式写"}]
    assert detect_correction_signal(msgs) is True


def test_detect_correction_chinese_gaiyong():
    msgs = [{"role": "user", "content": "改用别的方法"}]
    assert detect_correction_signal(msgs) is True


def test_detect_no_correction_positive():
    msgs = [{"role": "user", "content": "looks good, continue"}]
    assert detect_correction_signal(msgs) is False


def test_detect_no_correction_empty():
    assert detect_correction_signal([]) is False


def test_detect_no_correction_assistant_only():
    msgs = [{"role": "assistant", "content": "that's wrong from assistant side"}]
    assert detect_correction_signal(msgs) is False


def test_detect_correction_only_recent_user():
    # 20 irrelevant messages plus the correction
    old = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
    old.append({"role": "user", "content": "that's wrong"})
    assert detect_correction_signal(old) is True


def test_detect_correction_list_content():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "redo this"}]}]
    assert detect_correction_signal(msgs) is True


def test_detect_correction_non_string_non_list_skips():
    msgs = [{"role": "user", "content": 42}]
    assert detect_correction_signal(msgs) is False


def test_correction_patterns_is_nonempty():
    assert len(CORRECTION_PATTERNS) >= 10


# ── Fixture ──────────────────────────────────────────────

@pytest.fixture
def mock_deps(tmp_path):
    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(return_value=LLMResponse(
        content='{"workContext": "test", "topOfMind": "test", "facts": [{"content": "fact1", "category": "context", "confidence": 0.9}], "triples": []}',
        stop_reason="end_turn",
    ))
    # Default to non-streaming so existing tests exercise the `complete` path.
    # Tests that want to cover `stream_complete` can set
    # `mock_model.supports_streaming = True` and stub `mock_llm.stream_complete`.
    mock_model = MagicMock()
    mock_model.supports_streaming = False
    with patch("prax.core.memory_middleware.get_vector_store") as mock_vs_factory:
        mock_vs = AsyncMock()
        mock_vs.query = AsyncMock(return_value=[])
        mock_vs.sync_from_facts = AsyncMock()
        mock_vs_factory.return_value = mock_vs
        mw = MemoryExtractionMiddleware(
            cwd=str(tmp_path),
            llm_client=mock_llm,
            model_config=mock_model,
        )
        yield mw, mock_llm, mock_vs, tmp_path


def _make_state(messages=None, iteration=0):
    state = RuntimeState(
        messages=messages or [{"role": "user", "content": "test"}],
        context=MagicMock(),
        iteration=iteration,
        tool_loop_counts={},
        metadata={},
    )
    return state


# ── MemoryExtractionMiddleware.before_model ──────────────

@pytest.mark.asyncio
async def test_before_model_disabled(mock_deps):
    mw, _, _, _ = mock_deps
    mw.enabled = False
    state = _make_state()
    await mw.before_model(state)
    assert len(state.messages) == 1  # No injection


@pytest.mark.asyncio
async def test_before_model_injects_episodic_once(mock_deps):
    mw, _, _, tmp_path = mock_deps
    ep_dir = tmp_path / ".prax" / "sessions"
    ep_dir.mkdir(parents=True)
    (ep_dir / "2026-01-01-facts.json").write_text(json.dumps({
        "date": "2026-01-01",
        "facts": [{"content": "old fact", "category": "context", "confidence": 0.9}],
        "exchanges": [],
    }))
    state = _make_state()
    await mw.before_model(state)
    episodic_msgs = [m for m in state.messages if m.get("name") == "episodic_memory"]
    assert len(episodic_msgs) == 1


@pytest.mark.asyncio
async def test_before_model_skips_episodic_on_second_call(mock_deps):
    mw, _, _, _ = mock_deps
    mw._episodic_injected = True
    state = _make_state()
    await mw.before_model(state)
    episodic_msgs = [m for m in state.messages if m.get("name") == "episodic_memory"]
    assert len(episodic_msgs) == 0


@pytest.mark.asyncio
async def test_before_model_skips_same_iteration(mock_deps):
    mw, _, _, _ = mock_deps
    mw._episodic_injected = True
    mw._semantic_injected_turn = 0
    state = _make_state(iteration=0)
    await mw.before_model(state)
    semantic_msgs = [m for m in state.messages if m.get("name") in ("layered_memory", "semantic_memory")]
    assert len(semantic_msgs) == 0


@pytest.mark.asyncio
async def test_before_model_no_episodic_when_dir_missing(mock_deps):
    mw, _, _, _ = mock_deps
    # _episodic_injected is False but no dir exists → episodic_block is empty
    state = _make_state()
    await mw.before_model(state)
    episodic_msgs = [m for m in state.messages if m.get("name") == "episodic_memory"]
    assert len(episodic_msgs) == 0


# ── MemoryExtractionMiddleware.after_model ───────────────

@pytest.mark.asyncio
async def test_after_model_disabled(mock_deps):
    mw, _, _, _ = mock_deps
    mw.enabled = False
    state = _make_state()
    response = LLMResponse(content="test", stop_reason="end_turn")
    result = await mw.after_model(state, response)
    assert result == response


@pytest.mark.asyncio
async def test_after_model_skips_tool_calls(mock_deps):
    mw, _, _, _ = mock_deps
    state = _make_state()
    response = LLMResponse(
        content=[
            {"type": "text", "text": "x"},
            {"type": "tool_use", "id": "1", "name": "Read", "input": {}},
        ],
        stop_reason="tool_use",
    )
    result = await mw.after_model(state, response)
    assert result == response
    assert mw._extraction_task is None


@pytest.mark.asyncio
async def test_after_model_creates_extraction_task(mock_deps):
    mw, _, _, _ = mock_deps
    state = _make_state()
    response = LLMResponse(content=[{"type": "text", "text": "final answer"}], stop_reason="end_turn")
    result = await mw.after_model(state, response)
    assert result == response
    assert mw._extraction_task is not None


@pytest.mark.asyncio
async def test_after_model_returns_same_response(mock_deps):
    mw, _, _, _ = mock_deps
    state = _make_state()
    response = LLMResponse(content=[{"type": "text", "text": "hello"}], stop_reason="end_turn")
    result = await mw.after_model(state, response)
    assert result is response


# ── _latest_user_text ────────────────────────────────────

def test_latest_user_text_string(mock_deps):
    mw, _, _, _ = mock_deps
    msgs = [{"role": "user", "content": "hello world"}]
    assert mw._latest_user_text(msgs) == "hello world"


def test_latest_user_text_list(mock_deps):
    mw, _, _, _ = mock_deps
    msgs = [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]
    assert mw._latest_user_text(msgs) == "hello"


def test_latest_user_text_empty(mock_deps):
    mw, _, _, _ = mock_deps
    assert mw._latest_user_text([]) == ""


def test_latest_user_text_skips_assistant(mock_deps):
    mw, _, _, _ = mock_deps
    msgs = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "ignored"},
    ]
    assert mw._latest_user_text(msgs) == "first"


# ── _extract_and_save ────────────────────────────────────

@pytest.mark.asyncio
async def test_extract_and_save_success(mock_deps):
    mw, mock_llm, _, tmp_path = mock_deps
    (tmp_path / ".prax").mkdir(exist_ok=True)
    msgs = [{"role": "user", "content": "test"}]
    await mw._extract_and_save(msgs)
    mock_llm.complete.assert_called_once()


@pytest.mark.asyncio
async def test_extract_and_save_handles_code_block(mock_deps):
    mw, mock_llm, _, tmp_path = mock_deps
    (tmp_path / ".prax").mkdir(exist_ok=True)
    mock_llm.complete.return_value = LLMResponse(
        content='```json\n{"workContext":"","topOfMind":"","facts":[],"triples":[]}\n```',
        stop_reason="end_turn",
    )
    msgs = [{"role": "user", "content": "test"}]
    await mw._extract_and_save(msgs)  # Should not raise


@pytest.mark.asyncio
async def test_extract_and_save_handles_llm_failure(mock_deps):
    mw, mock_llm, _, _ = mock_deps
    mock_llm.complete.side_effect = Exception("LLM error")
    msgs = [{"role": "user", "content": "test"}]
    await mw._extract_and_save(msgs)  # Should not raise


@pytest.mark.asyncio
async def test_extract_and_save_handles_invalid_json(mock_deps):
    mw, mock_llm, _, _ = mock_deps
    mock_llm.complete.return_value = LLMResponse(
        content="not valid json at all",
        stop_reason="end_turn",
    )
    msgs = [{"role": "user", "content": "test"}]
    await mw._extract_and_save(msgs)  # Should not raise


# ── wait_for_pending_extraction ──────────────────────────
#
# Regression tests for the session-teardown drain. Before this fix,
# `prax prompt` closed the shared httpx client while the extraction task
# scheduled by after_model() was still awaiting an LLM response; the task
# surfaced `Cannot send a request, as the client has been closed` and
# MemoryStore.save() at memory_middleware.py:334 was never reached.

@pytest.mark.asyncio
async def test_wait_for_pending_extraction_noop_when_no_task(mock_deps):
    mw, _, _, _ = mock_deps
    assert mw._extraction_task is None
    await mw.wait_for_pending_extraction(timeout=0.01)  # must not raise


@pytest.mark.asyncio
async def test_wait_for_pending_extraction_drains_in_flight_task(mock_deps, tmp_path):
    mw, mock_llm, _, cwd = mock_deps
    (cwd / ".prax").mkdir(exist_ok=True)

    # Simulate a slow LLM that finishes within the drain budget.
    async def slow_complete(*args, **kwargs):
        await asyncio.sleep(0.05)
        return LLMResponse(
            content=[{"type": "text", "text": '{"workContext":"ctx","topOfMind":"","facts":[],"triples":[]}'}],
            stop_reason="end_turn",
        )
    mock_llm.complete = slow_complete

    state = _make_state()
    response = LLMResponse(content=[{"type": "text", "text": "done"}], stop_reason="end_turn")
    await mw.after_model(state, response)
    assert mw._extraction_task is not None
    assert not mw._extraction_task.done()

    await mw.wait_for_pending_extraction(timeout=5.0)
    assert mw._extraction_task.done()


@pytest.mark.asyncio
async def test_extract_and_save_uses_stream_complete_when_supported(mock_deps):
    # Some OpenAI-compatible proxies (e.g. third-party Codex relays) reject
    # non-streaming chat/completions with 400. Extraction must use the
    # streaming transport when the model_config advertises it — otherwise the
    # extraction call 400s and nothing persists even after the teardown drain.
    mw, mock_llm, _, tmp_path = mock_deps
    (tmp_path / ".prax").mkdir(exist_ok=True)
    mw.model_config.supports_streaming = True

    final_response = LLMResponse(
        content=[{"type": "text", "text": '{"workContext":"","topOfMind":"","facts":[],"triples":[]}'}],
        stop_reason="end_turn",
    )

    async def fake_stream(*args, **kwargs):
        yield "partial "
        yield "text"
        yield final_response

    mock_llm.stream_complete = fake_stream

    await mw._extract_and_save([{"role": "user", "content": "hi"}])
    # The non-streaming path must NOT have been used when streaming is on.
    assert mock_llm.complete.call_count == 0


@pytest.mark.asyncio
async def test_wait_for_pending_extraction_bounded_by_timeout(mock_deps):
    mw, mock_llm, _, _ = mock_deps

    async def very_slow(*args, **kwargs):
        await asyncio.sleep(10.0)
        return LLMResponse(
            content=[{"type": "text", "text": "{}"}],
            stop_reason="end_turn",
        )
    mock_llm.complete = very_slow

    state = _make_state()
    response = LLMResponse(content=[{"type": "text", "text": "hi"}], stop_reason="end_turn")
    await mw.after_model(state, response)

    t0 = time.monotonic()
    await mw.wait_for_pending_extraction(timeout=0.05)
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0, f"drain did not honour timeout; took {elapsed:.2f}s"

    # The shielded task is still running — cancel it explicitly so pytest
    # does not warn about an unawaited coroutine.
    mw._extraction_task.cancel()
    try:
        await mw._extraction_task
    except (asyncio.CancelledError, Exception):
        pass


# ── _format_messages ─────────────────────────────────────

def test_format_messages_string(mock_deps):
    mw, _, _, _ = mock_deps
    msgs = [{"role": "user", "content": "hello"}]
    result = mw._format_messages(msgs)
    assert "user: hello" in result


def test_format_messages_list(mock_deps):
    mw, _, _, _ = mock_deps
    msgs = [{"role": "assistant", "content": [
        {"type": "text", "text": "hi"},
        {"type": "tool_use", "name": "Read"},
        {"type": "tool_result"},
    ]}]
    result = mw._format_messages(msgs)
    assert "hi" in result
    assert "Tool: Read" in result


def test_format_messages_multiple_roles(mock_deps):
    mw, _, _, _ = mock_deps
    msgs = [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
    ]
    result = mw._format_messages(msgs)
    assert "user:" in result
    assert "assistant:" in result


def test_format_messages_empty(mock_deps):
    mw, _, _, _ = mock_deps
    result = mw._format_messages([])
    assert result == ""


# ── _merge_facts_with_confidence ─────────────────────────

def test_merge_facts_with_confidence_dedup(mock_deps):
    mw, _, _, _ = mock_deps
    existing = [{"content": "fact1", "category": "context", "confidence": 0.9,
                 "id": "f1", "createdAt": "", "source": "x"}]
    new = [{"content": "fact1", "category": "context", "confidence": 0.95}]
    result = mw._merge_facts_with_confidence(existing, new)
    assert len(result) == 1


def test_merge_facts_adds_new(mock_deps):
    mw, _, _, _ = mock_deps
    existing = [{"content": "fact1", "category": "context", "confidence": 0.9,
                 "id": "f1", "createdAt": "", "source": "x"}]
    new = [{"content": "fact2", "category": "knowledge", "confidence": 0.8}]
    result = mw._merge_facts_with_confidence(existing, new)
    assert len(result) == 2


def test_merge_facts_skips_low_confidence(mock_deps):
    mw, _, _, _ = mock_deps
    existing = []
    new = [{"content": "low fact", "category": "context", "confidence": 0.3}]
    result = mw._merge_facts_with_confidence(existing, new)
    assert len(result) == 0


def test_merge_facts_handles_string_format(mock_deps):
    mw, _, _, _ = mock_deps
    existing = ["old string fact"]
    new = [{"content": "new fact", "confidence": 0.9, "category": "context"}]
    result = mw._merge_facts_with_confidence(existing, new)
    assert len(result) == 2


def test_merge_facts_limits_to_max(mock_deps):
    mw, _, _, _ = mock_deps
    mw.max_facts = 2
    new = [{"content": f"fact{i}", "confidence": 0.9, "category": "context"} for i in range(5)]
    result = mw._merge_facts_with_confidence([], new)
    assert len(result) == 2


def test_merge_facts_empty_new(mock_deps):
    mw, _, _, _ = mock_deps
    existing = [{"content": "existing", "confidence": 0.9, "category": "context",
                 "id": "f1", "createdAt": "", "source": "x"}]
    result = mw._merge_facts_with_confidence(existing, [])
    assert len(result) == 1


# ── _write_episodic_snapshot ─────────────────────────────

def test_write_episodic_snapshot(mock_deps):
    mw, _, _, tmp_path = mock_deps
    facts = [{"content": "test", "confidence": 0.9, "category": "context"}]
    mw._write_episodic_snapshot(facts)
    ep_dir = tmp_path / ".prax" / "sessions"
    files = list(ep_dir.glob("*-facts.json"))
    assert len(files) == 1


def test_write_episodic_snapshot_dedup(mock_deps):
    mw, _, _, tmp_path = mock_deps
    facts = [{"content": "test", "confidence": 0.9, "category": "context"}]
    mw._write_episodic_snapshot(facts)
    mw._write_episodic_snapshot(facts)  # Write again
    ep_dir = tmp_path / ".prax" / "sessions"
    files = list(ep_dir.glob("*-facts.json"))
    data = json.loads(files[0].read_text())
    assert len(data["facts"]) == 1  # Not duplicated


def test_write_episodic_skip_no_facts(mock_deps):
    mw, _, _, tmp_path = mock_deps
    mw._write_episodic_snapshot([])
    ep_dir = tmp_path / ".prax" / "sessions"
    assert not ep_dir.exists()


def test_write_episodic_skips_low_confidence(mock_deps):
    mw, _, _, tmp_path = mock_deps
    facts = [{"content": "low conf", "confidence": 0.2, "category": "context"}]
    mw._write_episodic_snapshot(facts)
    ep_dir = tmp_path / ".prax" / "sessions"
    assert not ep_dir.exists()


# ── _load_episodic_memory ────────────────────────────────

def test_load_episodic_memory_no_dir(mock_deps):
    mw, _, _, _ = mock_deps
    result = mw._load_episodic_memory()
    assert result == ""


def test_load_episodic_memory_with_files(mock_deps):
    mw, _, _, tmp_path = mock_deps
    ep_dir = tmp_path / ".prax" / "sessions"
    ep_dir.mkdir(parents=True)
    (ep_dir / "2026-01-01-facts.json").write_text(json.dumps({
        "date": "2026-01-01",
        "facts": [{"content": "fact1", "category": "context"}],
        "exchanges": [{"user": "hello", "assistant": "hi"}],
    }))
    result = mw._load_episodic_memory()
    assert "episodic_memory" in result
    assert "fact1" in result


def test_load_episodic_memory_limits_days(mock_deps):
    mw, _, _, tmp_path = mock_deps
    mw._episodic_days = 1
    ep_dir = tmp_path / ".prax" / "sessions"
    ep_dir.mkdir(parents=True)
    for i in range(5):
        (ep_dir / f"2026-01-0{i+1}-facts.json").write_text(json.dumps({
            "date": f"2026-01-0{i+1}",
            "facts": [{"content": f"fact{i}", "category": "context"}],
            "exchanges": [],
        }))
    result = mw._load_episodic_memory()
    # Should only load 1 day (the newest one: 2026-01-05, fact4)
    assert "fact4" in result


def test_load_episodic_memory_empty_dir(mock_deps):
    mw, _, _, tmp_path = mock_deps
    ep_dir = tmp_path / ".prax" / "sessions"
    ep_dir.mkdir(parents=True)
    # Dir exists but no files
    result = mw._load_episodic_memory()
    assert result == ""


# ── _chunk_exchanges ─────────────────────────────────────

def test_chunk_exchanges(mock_deps):
    mw, _, _, _ = mock_deps
    msgs = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    exchanges = mw._chunk_exchanges(msgs)
    assert len(exchanges) == 1
    assert exchanges[0]["user"] == "hello"
    assert exchanges[0]["assistant"] == "hi there"


def test_chunk_exchanges_list_content(mock_deps):
    mw, _, _, _ = mock_deps
    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        {"role": "assistant", "content": "hi"},
    ]
    exchanges = mw._chunk_exchanges(msgs)
    assert len(exchanges) == 1
    assert "hello" in exchanges[0]["user"]


def test_chunk_exchanges_empty(mock_deps):
    mw, _, _, _ = mock_deps
    assert mw._chunk_exchanges([]) == []


def test_chunk_exchanges_user_only(mock_deps):
    mw, _, _, _ = mock_deps
    msgs = [{"role": "user", "content": "standalone question"}]
    exchanges = mw._chunk_exchanges(msgs)
    assert len(exchanges) == 1
    assert exchanges[0]["assistant"] == ""


# ── _write_triples_to_kg ─────────────────────────────────

def test_write_triples_no_backend(mock_deps):
    mw, _, _, _ = mock_deps
    mw._memory_backend = None
    mw._write_triples_to_kg({"triples": [["a", "b", "c"]]}, False)  # Should not raise


def test_write_triples_valid(mock_deps):
    mw, _, _, _ = mock_deps
    mock_backend = MagicMock()
    mock_kg = MagicMock()
    mock_backend.get_knowledge_graph.return_value = mock_kg
    mw._memory_backend = mock_backend
    mw._write_triples_to_kg({"triples": [["user", "prefers", "python"]]}, False)
    mock_kg.add_triples_batch.assert_called_once()


def test_write_triples_kg_none(mock_deps):
    mw, _, _, _ = mock_deps
    mock_backend = MagicMock()
    mock_backend.get_knowledge_graph.return_value = None
    mw._memory_backend = mock_backend
    mw._write_triples_to_kg({"triples": [["a", "b", "c"]]}, False)  # Should not raise


def test_write_triples_empty(mock_deps):
    mw, _, _, _ = mock_deps
    mock_backend = MagicMock()
    mock_kg = MagicMock()
    mock_backend.get_knowledge_graph.return_value = mock_kg
    mw._memory_backend = mock_backend
    mw._write_triples_to_kg({"triples": []}, False)
    mock_kg.add_triples_batch.assert_not_called()


def test_write_triples_kg_exception(mock_deps):
    mw, _, _, _ = mock_deps
    mock_backend = MagicMock()
    mock_backend.get_knowledge_graph.side_effect = RuntimeError("db error")
    mw._memory_backend = mock_backend
    mw._write_triples_to_kg({"triples": [["a", "b", "c"]]}, False)  # Should not raise
