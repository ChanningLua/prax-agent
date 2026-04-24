"""Memory extraction middleware for LLM-driven knowledge accumulation."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .llm_client import LLMClient, LLMResponse, ModelConfig
from .memory_store import MemoryStore, MemoryEntry
from .middleware import AgentMiddleware, RuntimeState
from .memory.vector_store import get_vector_store
from .memory.layers import LayeredInjector

logger = logging.getLogger(__name__)


EXTRACTION_PROMPT = """Analyze the recent conversation and extract persistent knowledge.

{correction_hint}

Return a JSON object with these fields:
- workContext: Brief project background (update only if new info appears)
- topOfMind: Current most important tasks or focus areas
- facts: Array of fact objects, each with:
  - content: The fact content (string)
  - category: One of [preference, knowledge, context, behavior, goal, correction]
  - confidence: Float between 0.0 and 1.0 (use >= 0.7 for facts worth persisting)
- triples: Array of [subject, predicate, object] triples.
  Examples:
    ["user", "prefers", "Chinese language"]
    ["用户", "偏好", "中文回答"]
    ["project", "uses", "SQLite"]
  Rules:
    - Only include stable, reusable knowledge.
    - Use lowercase, concise terms.
    - Multi-word entities are OK: "Chinese language", "dark mode".
    - The predicate should be a verb or short phrase (e.g. "uses", "prefers", "works_on").
    - Subject and object must be ≤200 chars, predicate ≤50 chars.

Keep it concise. Only include information worth remembering across sessions.

Recent messages:
{messages}

Return ONLY valid JSON, no other text."""

COMPOUND_PROMPT = """Analyze this conversation. Extract problem-solution patterns ONLY if ALL conditions are met:
1. A concrete error, bug, or blocker was encountered
2. A working solution was found and verified
3. The solution is reusable in other contexts

Return a JSON array (may be empty if conditions not met):
[{{"problem": "concise problem description", "what_failed": ["approach1 that failed", "approach2 that failed"], "solution": "what actually worked", "prevention": "how to avoid this in future"}}]

Messages:
{messages}

Return ONLY valid JSON array, no other text."""

# Correction signal patterns (multilingual)
CORRECTION_PATTERNS = [
    r"\bthat(?:'s| is) (?:wrong|incorrect)\b",
    r"\byou misunderstood\b",
    r"\btry again\b",
    r"\bredo\b",
    r"不对",
    r"你理解错了",
    r"你理解有误",
    r"重试",
    r"重新来",
    r"换一种",
    r"改用",
]


def detect_correction_signal(messages: list[dict[str, Any]]) -> bool:
    """Detect explicit user corrections in recent conversation turns.

    Checks the last 6 user messages for correction patterns.
    """
    recent_user_msgs = [
        msg for msg in messages[-12:]  # Check last 12 messages
        if msg.get("role") == "user"
    ][-6:]  # Keep last 6 user messages

    for msg in recent_user_msgs:
        content = msg.get("content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            # Extract text from content blocks
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
            text = " ".join(text_parts)
        else:
            continue

        # Check for correction patterns
        for pattern in CORRECTION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return True

    return False


class MemoryExtractionMiddleware(AgentMiddleware):
    """Middleware that extracts and persists knowledge after final responses.

    Writes facts to both the legacy MemoryStore (for sync compatibility) and
    the MemoryBackend (when provided) so both code paths stay consistent.
    """

    def __init__(
        self,
        *,
        cwd: str,
        llm_client: LLMClient,
        model_config: ModelConfig,
        enabled: bool = True,
        fact_confidence_threshold: float = 0.7,
        max_facts: int = 100,
        memory_backend: Any = None,   # MemoryBackend | None
        semantic_top_k: int = 5,      # facts to inject via semantic search
        episodic_days: int = 3,       # how many recent episodic files to load
    ):
        self.cwd = cwd
        self.llm_client = llm_client
        self.model_config = model_config
        self.enabled = enabled
        self.fact_confidence_threshold = fact_confidence_threshold
        self.max_facts = max_facts
        self.store = MemoryStore(cwd)
        self._memory_backend = memory_backend
        self._extraction_task: asyncio.Task | None = None
        self._last_extraction_time = 0.0
        self._semantic_top_k = semantic_top_k
        self._vector_store = get_vector_store()
        self._semantic_injected_turn: int = -1  # track per-turn injection
        self._episodic_days = episodic_days
        self._episodic_injected: bool = False   # inject once per session

        # Build LayeredInjector for tiered memory injection
        kg = None
        if memory_backend is not None:
            try:
                kg = memory_backend.get_knowledge_graph(cwd)
            except Exception:
                pass
        self._layered_injector = LayeredInjector(
            kg=kg,
            vector_store=self._vector_store,
            memory_store=self.store,
            memory_backend=memory_backend,
        )

    async def before_model(self, state: RuntimeState) -> None:
        """Inject layered memory context before the model call.

        On the first call of a session, also injects episodic memory from
        recent sessions (.prax/sessions/{date}-facts.json).
        Uses LayeredInjector for tiered L0-L3 injection.
        """
        if not self.enabled:
            return

        # Episodic memory injection — once per session, before semantic
        if not self._episodic_injected:
            self._episodic_injected = True
            episodic_block = self._load_episodic_memory()
            if episodic_block:
                state.messages.append({
                    "role": "user",
                    "name": "episodic_memory",
                    "content": episodic_block,
                })

        if state.iteration == self._semantic_injected_turn:
            return
        self._semantic_injected_turn = state.iteration

        # Build query from the last user message
        query = self._latest_user_text(state.messages)
        if not query:
            return

        # Use LayeredInjector for tiered memory injection
        try:
            layered_block = await self._layered_injector.build_async(
                self.cwd, query=query
            )
        except Exception:
            layered_block = ""

        if layered_block:
            state.messages.append({
                "role": "user",
                "name": "layered_memory",
                "content": f"<layered_memory>\n{layered_block}\n</layered_memory>",
            })
            return

        # Fallback: direct vector store query if layered injection returned nothing
        try:
            results = await self._vector_store.query(
                self.cwd, query, n_results=self._semantic_top_k
            )
        except Exception:
            return

        if not results:
            return

        lines = []
        for item in results:
            meta = item.get("metadata", {})
            category = meta.get("category", "context")
            score = item.get("score", 0)
            lines.append(f"- [{category}] {item['content']} (relevance: {score:.2f})")

        block = (
            "<semantic_memory>\n"
            "Relevant facts from memory (ranked by similarity to current task):\n"
            + "\n".join(lines)
            + "\n</semantic_memory>"
        )
        state.messages.append({
            "role": "user",
            "name": "semantic_memory",
            "content": block,
        })

    def _latest_user_text(self, messages: list[dict[str, Any]]) -> str:
        """Extract text from the most recent user message."""
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                return content[:500]
            if isinstance(content, list):
                parts = [
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                text = " ".join(parts).strip()
                if text:
                    return text[:500]
        return ""

    async def after_model(self, state: RuntimeState, response: LLMResponse) -> LLMResponse:
        """Trigger extraction after final responses (non-tool-call turns)."""
        if not self.enabled:
            return response

        # Only extract on final responses (no tool calls)
        if response.has_tool_calls:
            return response

        # Debounce: 2 second delay to avoid rapid re-triggers
        current_time = asyncio.get_event_loop().time()
        if current_time - self._last_extraction_time < 2.0:
            return response

        self._last_extraction_time = current_time

        # Schedule extraction in background (don't block main flow)
        if self._extraction_task is not None and not self._extraction_task.done():
            self._extraction_task.cancel()

        self._extraction_task = asyncio.create_task(
            self._extract_and_save(state.messages)
        )

        return response

    async def wait_for_pending_extraction(self, timeout: float = 15.0) -> None:
        """Block until any in-flight extraction task finishes, bounded by timeout.

        Called from the session teardown path so the background task scheduled
        by ``after_model`` can reach ``self.store.save`` before the shared
        httpx client is closed. Without this drain, closing the client races
        the task and surfaces as
        ``Memory extraction failed: Cannot send a request, as the client has
        been closed`` — meaning nothing from the session actually persists.

        Best-effort: a stuck LLM extraction must not hang CLI exit, so the
        timeout simply stops waiting and returns. The task keeps running under
        a shield so it still has a chance to finish before process exit.
        """
        task = self._extraction_task
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "Memory extraction exceeded %.0fs budget on exit; session state may be partial.",
                timeout,
            )
        except Exception:
            pass

    async def _extraction_llm_call(self, prompt: str) -> LLMResponse:
        """Run a one-shot extraction prompt against the model, choosing transport
        based on provider capability.

        Some OpenAI-compatible proxies (e.g. third-party Codex relays) reject
        non-streaming requests with `400 Stream must be set to true`. The main
        agent loop already handles this via ``stream_complete`` — extraction
        needs the same treatment or the response never makes it back and
        nothing persists. Falls back to the plain ``complete`` path for
        providers that don't support streaming.
        """
        messages = [{"role": "user", "content": prompt}]
        system_prompt = "You are a knowledge extraction assistant. Return only valid JSON."

        if self.model_config.supports_streaming:
            response: LLMResponse | None = None
            async for chunk in self.llm_client.stream_complete(
                messages=messages,
                tools=[],
                model_config=self.model_config,
                system_prompt=system_prompt,
                thinking_enabled=False,
            ):
                if not isinstance(chunk, str):
                    response = chunk
            if response is None:
                response = LLMResponse(content=[{"type": "text", "text": ""}])
            return response

        return await self.llm_client.complete(
            messages=messages,
            tools=[],
            model_config=self.model_config,
            system_prompt=system_prompt,
            thinking_enabled=False,
            reasoning_effort=None,
        )

    async def _extract_and_save(self, messages: list[dict[str, Any]]) -> None:
        """Extract knowledge from recent messages and save to memory."""
        try:
            # Take last 20 messages for context
            recent = messages[-20:] if len(messages) > 20 else messages

            # Detect correction signals
            correction_detected = detect_correction_signal(messages)
            correction_hint = ""
            if correction_detected:
                correction_hint = (
                    "IMPORTANT: Explicit correction signals were detected in this conversation. "
                    "Pay special attention to what was wrong, what was corrected, "
                    "and record the correct approach as a fact with category 'correction' "
                    "and confidence >= 0.95 when appropriate."
                )

            # Format messages for extraction prompt
            formatted = self._format_messages(recent)
            extraction_prompt = EXTRACTION_PROMPT.format(
                messages=formatted,
                correction_hint=correction_hint
            )

            # Call LLM with lightweight model (streams when provider supports it)
            response = await self._extraction_llm_call(extraction_prompt)

            # Parse response
            text = response.text.strip()
            # Remove markdown code blocks if present
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1]) if len(lines) > 2 else text

            data = json.loads(text)

            # Merge with existing memory
            existing = self.store.load()
            updated = MemoryEntry(
                workContext=data.get("workContext") or existing.workContext,
                topOfMind=data.get("topOfMind") or existing.topOfMind,
                facts=self._merge_facts_with_confidence(existing.facts, data.get("facts", []))
            )

            self.store.save(updated)

            # Sync all facts to vector store for semantic retrieval
            try:
                await self._vector_store.sync_from_facts(self.cwd, updated.facts)
            except Exception as e:
                logger.warning("Vector store sync failed (non-fatal): %s", e)

            # Write episodic snapshot for today
            try:
                self._write_episodic_snapshot(data.get("facts", []))
            except Exception as e:
                logger.warning("Episodic write failed (non-fatal): %s", e)

            # Compound solution extraction — triggered on correction signals
            if correction_detected:
                asyncio.create_task(self._extract_compound(recent))

            # Also persist to MemoryBackend when available
            if self._memory_backend is not None:
                try:
                    from .memory.backend import Fact, MemoryContext
                    # Sync updated context
                    if updated.workContext or updated.topOfMind:
                        await self._memory_backend.save_context(
                            self.cwd,
                            MemoryContext(
                                work_context=updated.workContext,
                                top_of_mind=updated.topOfMind,
                            ),
                        )
                    # Store new high-confidence facts
                    now = datetime.now(timezone.utc).isoformat()
                    for fact_dict in data.get("facts", []):
                        confidence = fact_dict.get("confidence", 0.5)
                        if confidence >= self.fact_confidence_threshold:
                            content = str(fact_dict.get("content", "")).strip()
                            if content:
                                await self._memory_backend.store_fact(
                                    self.cwd,
                                    Fact(
                                        id=f"fact_{uuid.uuid4().hex[:8]}",
                                        content=content,
                                        category=fact_dict.get("category", "context"),
                                        confidence=confidence,
                                        created_at=now,
                                        source="extraction",
                                    ),
                                )
                except Exception as e:
                    logger.warning("Memory backend write failed (non-fatal): %s", e)

            # Write triples to KnowledgeGraph when available
            self._write_triples_to_kg(data, correction_detected)

        except Exception as e:
            logger.warning("Memory extraction failed (best-effort, non-fatal): %s", e)

    def _write_triples_to_kg(
        self, data: dict[str, Any], correction_detected: bool
    ) -> None:
        """Write extracted triples to the KnowledgeGraph."""
        if self._memory_backend is None:
            return
        try:
            kg = self._memory_backend.get_knowledge_graph(self.cwd)
            if kg is None:
                return

            triples = data.get("triples", [])
            valid_triples: list[tuple[str, str, str]] = []
            for triple in triples:
                if isinstance(triple, (list, tuple)) and len(triple) >= 3:
                    subject, predicate, obj = str(triple[0]).strip(), str(triple[1]).strip(), str(triple[2]).strip()
                    # Validate: non-empty and within length limits
                    if (subject and predicate and obj
                            and len(subject) <= 200 and len(predicate) <= 50 and len(obj) <= 200):
                        valid_triples.append((subject, predicate, obj))

            if valid_triples:
                kg.add_triples_batch(valid_triples, source="extraction")

            # On correction signals, try to invalidate contradicted triples
            if correction_detected:
                for fact in data.get("facts", []):
                    if isinstance(fact, dict) and fact.get("category") == "correction":
                        source_error = fact.get("content", "")
                        # Look for triples to invalidate based on correction context
                        invalidations = data.get("invalidations", [])
                        for inv in invalidations:
                            if isinstance(inv, (list, tuple)) and len(inv) >= 3:
                                kg.invalidate(str(inv[0]), str(inv[1]), str(inv[2]))
        except Exception as e:
            logger.warning("KG triple write failed (non-fatal): %s", e)

    def _episodic_dir(self) -> Path:
        return Path(self.cwd) / ".prax" / "sessions"

    def _solutions_dir(self) -> Path:
        return Path(self.cwd) / ".prax" / "solutions"

    @staticmethod
    def _chunk_exchanges(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        """Chunk messages into user+assistant exchange pairs.

        Each exchange is a dict with 'user' and 'assistant' keys.
        Assistant responses are limited to the first 8 lines.
        """
        exchanges: list[dict[str, str]] = []
        i = 0
        while i < len(messages):
            msg = messages[i]
            if msg.get("role") == "user":
                user_text = msg.get("content", "")
                if isinstance(user_text, list):
                    parts = [
                        b.get("text", "") for b in user_text
                        if isinstance(b, dict) and b.get("type") == "text"
                    ]
                    user_text = " ".join(parts)
                user_text = str(user_text)[:300]

                # Look for the next assistant message
                assistant_text = ""
                if i + 1 < len(messages) and messages[i + 1].get("role") == "assistant":
                    a_content = messages[i + 1].get("content", "")
                    if isinstance(a_content, list):
                        parts = [
                            b.get("text", "") for b in a_content
                            if isinstance(b, dict) and b.get("type") == "text"
                        ]
                        a_content = " ".join(parts)
                    # Limit to first 8 lines
                    lines = str(a_content).split("\n")[:8]
                    assistant_text = "\n".join(lines)[:500]
                    i += 1

                if user_text.strip():
                    exchanges.append({"user": user_text.strip(), "assistant": assistant_text.strip()})
            i += 1
        return exchanges

    async def _extract_compound(self, messages: list[dict[str, Any]]) -> None:
        """Extract structured problem-solution patterns and write to .prax/solutions/."""
        try:
            formatted = self._format_messages(messages)
            prompt = COMPOUND_PROMPT.format(messages=formatted)

            response = await self._extraction_llm_call(prompt)

            text = response.text.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1]) if len(lines) > 2 else text

            solutions = json.loads(text)
            if not isinstance(solutions, list):
                return

            for item in solutions:
                if not isinstance(item, dict):
                    continue
                problem = str(item.get("problem", "")).strip()
                if not problem:
                    continue
                self._write_compound_solution(
                    problem=problem,
                    what_failed=item.get("what_failed", []),
                    solution=str(item.get("solution", "")).strip(),
                    prevention=str(item.get("prevention", "")).strip(),
                )
        except Exception as e:
            logger.warning("Compound extraction failed (non-fatal): %s", e)

    def _write_compound_solution(
        self,
        *,
        problem: str,
        what_failed: list,
        solution: str,
        prevention: str,
    ) -> None:
        """Write a structured solution document to .prax/solutions/."""
        solutions_dir = self._solutions_dir()
        solutions_dir.mkdir(parents=True, exist_ok=True)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Build slug from problem text (first 40 chars, sanitized)
        slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", problem[:40]).strip("-").lower()
        slug = slug[:40]
        filename = f"{today}-{slug}.md"
        filepath = solutions_dir / filename

        failed_lines = "\n".join(f"- {f}" for f in what_failed) if what_failed else "- (not recorded)"

        content = f"""# Problem: {problem}
Date: {today}

## What Didn't Work
{failed_lines}

## Solution
{solution or "(not recorded)"}

## Prevention
{prevention or "(not recorded)"}
"""
        filepath.write_text(content, encoding="utf-8")
        logger.info("Compound solution written: %s", filepath)

    def _write_episodic_snapshot(
        self, new_facts: list[dict[str, Any]], messages: list[dict[str, Any]] | None = None
    ) -> None:
        """Append high-confidence facts and exchange pairs to today's episodic file.

        File: .prax/sessions/{YYYY-MM-DD}-facts.json
        Format: {"date": "...", "facts": [...], "exchanges": [...]}
        """
        high_conf = [
            f for f in new_facts
            if isinstance(f, dict)
            and f.get("confidence", 0) >= self.fact_confidence_threshold
            and f.get("content", "").strip()
        ]

        exchanges = self._chunk_exchanges(messages) if messages else []

        if not high_conf and not exchanges:
            return

        ep_dir = self._episodic_dir()
        ep_dir.mkdir(parents=True, exist_ok=True)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        ep_file = ep_dir / f"{today}-facts.json"

        existing: list[dict[str, Any]] = []
        if ep_file.exists():
            try:
                existing = json.loads(ep_file.read_text(encoding="utf-8")).get("facts", [])
            except Exception:
                existing = []

        # Deduplicate by content
        existing_keys = {f.get("content", "").strip().lower() for f in existing}
        for fact in high_conf:
            key = fact.get("content", "").strip().lower()
            if key and key not in existing_keys:
                existing.append({
                    "content": fact["content"],
                    "category": fact.get("category", "context"),
                    "confidence": fact.get("confidence", 0.7),
                    "source": fact.get("source", "extraction"),
                })
                existing_keys.add(key)

        ep_file.write_text(
            json.dumps({"date": today, "facts": existing, "exchanges": exchanges}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_episodic_memory(self) -> str:
        """Load facts and exchanges from the most recent episodic files.

        Reads up to `_episodic_days` most recent {date}-facts.json files.
        Returns empty string if no episodic data exists.
        """
        ep_dir = self._episodic_dir()
        if not ep_dir.exists():
            return ""

        # Find all episodic files, sorted newest first
        ep_files = sorted(
            ep_dir.glob("*-facts.json"),
            key=lambda p: p.stem,
            reverse=True,
        )[: self._episodic_days]

        if not ep_files:
            return ""

        all_facts: list[str] = []
        all_exchanges: list[str] = []
        for ep_file in ep_files:
            try:
                data = json.loads(ep_file.read_text(encoding="utf-8"))
                date_str = data.get("date", ep_file.stem.replace("-facts", ""))
                for fact in data.get("facts", []):
                    content = fact.get("content", "").strip()
                    category = fact.get("category", "context")
                    if content:
                        all_facts.append(f"- [{date_str}][{category}] {content}")
                # Read exchanges (backward-compatible: field is optional)
                for ex in data.get("exchanges", []):
                    if isinstance(ex, dict) and ex.get("user"):
                        line = f"- [{date_str}] Q: {ex['user']}"
                        if ex.get("assistant"):
                            line += f" → A: {ex['assistant'][:100]}"
                        all_exchanges.append(line)
            except Exception:
                continue

        if not all_facts and not all_exchanges:
            return ""

        parts = []
        if all_facts:
            parts.append(
                f"Facts learned in recent sessions (last {self._episodic_days} days):\n"
                + "\n".join(all_facts)
            )
        if all_exchanges:
            parts.append(
                "Recent exchanges:\n" + "\n".join(all_exchanges[:10])
            )

        return "<episodic_memory>\n" + "\n".join(parts) + "\n</episodic_memory>"

    def _format_messages(self, messages: list[dict[str, Any]]) -> str:
        """Format messages for extraction prompt."""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            # Handle different content formats
            if isinstance(content, list):
                # Extract text from content blocks
                text_parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif block.get("type") == "tool_use":
                            text_parts.append(f"[Tool: {block.get('name')}]")
                        elif block.get("type") == "tool_result":
                            text_parts.append("[Tool result]")
                content = " ".join(text_parts)

            lines.append(f"{role}: {content[:200]}")  # Limit length

        return "\n".join(lines)

    def _merge_facts_with_confidence(
        self,
        existing: list[dict[str, Any]],
        new: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Merge new facts with existing, with confidence-based filtering and deduplication.

        Args:
            existing: Existing facts (list of dicts)
            new: New facts from LLM extraction (list of dicts or strings)

        Returns:
            Merged facts list, sorted by confidence, limited to max_facts
        """
        # Normalize existing facts to dict format
        normalized_existing = []
        for fact in existing:
            if isinstance(fact, dict):
                normalized_existing.append(fact)
            elif isinstance(fact, str):
                # Migrate old string format
                now = datetime.now(timezone.utc).isoformat()
                normalized_existing.append({
                    "id": f"fact_{uuid.uuid4().hex[:8]}",
                    "content": fact,
                    "category": "context",
                    "confidence": 0.8,
                    "createdAt": now,
                    "source": "migration",
                })

        # Build deduplication key set from existing facts
        existing_keys = {
            self.store._normalize_fact_key(f.get("content", ""))
            for f in normalized_existing
        }

        # Process new facts
        merged = list(normalized_existing)
        now = datetime.now(timezone.utc).isoformat()

        for fact in new:
            # Handle both dict and string formats
            if isinstance(fact, str):
                content = fact
                category = "context"
                confidence = 0.5
            elif isinstance(fact, dict):
                content = fact.get("content", "")
                category = fact.get("category", "context")
                confidence = fact.get("confidence", 0.5)
            else:
                continue

            # Skip if confidence below threshold
            if confidence < self.fact_confidence_threshold:
                continue

            # Normalize and check for duplicates
            normalized_content = content.strip()
            if not normalized_content:
                continue

            fact_key = self.store._normalize_fact_key(normalized_content)
            if fact_key in existing_keys:
                continue  # Skip duplicate

            # Add new fact
            merged.append({
                "id": f"fact_{uuid.uuid4().hex[:8]}",
                "content": normalized_content,
                "category": category,
                "confidence": max(0.0, min(1.0, confidence)),  # Clamp to [0, 1]
                "createdAt": now,
                "source": "extraction",
            })
            existing_keys.add(fact_key)

        # Sort by confidence (descending) and limit to max_facts
        merged = sorted(
            merged,
            key=lambda f: f.get("confidence", 0.5),
            reverse=True
        )[:self.max_facts]

        return merged
