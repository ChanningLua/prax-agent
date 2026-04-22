"""End-to-end: cron dispatcher → notify.yaml → provider HTTP call.

Covers the glue layer that `test_cron_dispatcher.py` mocks out: the default
notifier built by `_build_default_notifier` reading `.prax/notify.yaml` and
dispatching through the same `build_provider` used by NotifyTool.
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import patch

import httpx
import pytest

from prax.commands.cron import run_due_jobs
from prax.core.cron_store import CronJob, CronStore


@pytest.mark.asyncio
async def test_cron_notify_end_to_end_hits_provider(tmp_path, monkeypatch):
    """Seed real cron.yaml + notify.yaml, run dispatcher, observe HTTP payload."""
    # 1. A cron job that is due right now and wants success notifications.
    store = CronStore(str(tmp_path))
    store.add(CronJob(
        name="e2e",
        schedule="* * * * *",
        prompt="doesn't matter — runner is stubbed",
        notify_on=["success"],
        notify_channel="daily",
    ))

    # 2. A real notify.yaml pointing at a captured webhook.
    (tmp_path / ".prax" / "notify.yaml").write_text(
        "channels:\n"
        "  daily:\n"
        "    provider: feishu_webhook\n"
        "    url: https://open.feishu.cn/hook/e2e\n"
        "    default_title_prefix: \"[Prax] \"\n"
    )

    captured: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json={"code": 0, "msg": "ok"})

    transport = httpx.MockTransport(handler)

    # Stub out httpx.AsyncClient to always use the mock transport.
    real_client_cls = httpx.AsyncClient

    def make_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client_cls(*args, **kwargs)

    # 3. Stubbed runner — pretend the prax subprocess succeeded without
    #    actually spawning anything.
    async def fake_runner(argv, log_path):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("simulated run output\n")
        return 0, "simulated run output\n", ""

    now = datetime(2026, 4, 22, 12, 0)

    with patch.object(httpx, "AsyncClient", make_client):
        results = await run_due_jobs(
            str(tmp_path),
            now=now,
            runner=fake_runner,
            # notifier intentionally NOT injected — we want the default
            # _build_default_notifier path that reads notify.yaml itself.
        )

    # Dispatcher ran the job and marked it success.
    assert len(results) == 1
    assert results[0].status == "success"
    assert results[0].log_path.exists()

    # Exactly one webhook call landed, with the right shape.
    assert len(captured) == 1
    url, payload = captured[0]
    assert url == "https://open.feishu.cn/hook/e2e"
    assert payload["msg_type"] == "interactive"
    card = payload["card"]
    # Title carries the configured prefix + the cron job name + status.
    assert card["header"]["title"]["content"].startswith("[Prax] ")
    assert "e2e" in card["header"]["title"]["content"]
    assert "success" in card["header"]["title"]["content"]
    # Info-level success maps to blue card template (from FeishuWebhookProvider).
    assert card["header"]["template"] == "blue"
    # Body (markdown) mentions the schedule, status, and log path.
    body = card["elements"][0]["content"]
    assert "e2e" in body
    assert "* * * * *" in body
    assert "success" in body


@pytest.mark.asyncio
async def test_cron_notify_end_to_end_does_not_notify_when_channel_missing(tmp_path):
    """If cron.yaml references a channel that notify.yaml doesn't define,
    the dispatcher must still run the job and just log a warning — not crash."""
    store = CronStore(str(tmp_path))
    store.add(CronJob(
        name="e2e",
        schedule="* * * * *",
        prompt="x",
        notify_on=["success"],
        notify_channel="nonexistent",
    ))
    # Deliberately no notify.yaml.

    async def fake_runner(argv, log_path):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("ok")
        return 0, "ok", ""

    now = datetime(2026, 4, 22, 12, 0)
    # Should not raise even though the channel is missing.
    results = await run_due_jobs(str(tmp_path), now=now, runner=fake_runner)
    assert results[0].status == "success"
