import json
from datetime import datetime, timezone

from prax.core.memory_middleware import MemoryExtractionMiddleware


def test_load_episodic_memory_reads_session_facts(tmp_path):
    sessions_dir = tmp_path / ".prax" / "sessions"
    sessions_dir.mkdir(parents=True)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    episodic_file = sessions_dir / f"{today}-facts.json"
    episodic_file.write_text(
        json.dumps(
            {
                "date": today,
                "facts": [
                    {
                        "content": "The users table has columns: id, email, created_at.",
                        "category": "knowledge",
                        "confidence": 0.95,
                        "source": "test",
                    }
                ],
            }
        )
    )

    middleware = MemoryExtractionMiddleware(
        cwd=str(tmp_path),
        llm_client=None,
        model_config=None,
    )

    injected = middleware._load_episodic_memory()

    assert "<episodic_memory>" in injected
    assert "users table has columns: id, email, created_at." in injected
    assert today in injected
