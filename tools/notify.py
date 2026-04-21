"""NotifyTool — send notifications through configured channels.

Channels live in ``.prax/notify.yaml`` (merged with ``~/.prax/notify.yaml``).
Each channel picks a provider; providers are deliberately minimal:

- ``feishu_webhook`` / ``lark_webhook``: HTTP POST an interactive card to a
  group bot webhook.
- ``smtp``: send email, with the password read from an environment variable
  (never embedded in YAML).

Personal WeChat is intentionally not supported here — the stable way to reach
WeChat is via the 企业微信 webhook, which can be added as a provider later.
"""

from __future__ import annotations

import logging
import os
import smtplib
from abc import ABC, abstractmethod
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Callable

import httpx

from .base import PermissionLevel, Tool, ToolResult

logger = logging.getLogger(__name__)


_LEVEL_TO_FEISHU_TEMPLATE = {"info": "blue", "warn": "orange", "error": "red"}


def _expand_env(value: str) -> str:
    return os.path.expandvars(value)


class NotifyProvider(ABC):
    """Outbound channel. ``send`` must raise on failure."""

    @abstractmethod
    async def send(self, *, title: str, body: str, level: str) -> None: ...


class _WebhookCardProvider(NotifyProvider):
    """Shared implementation for Feishu/Lark interactive-card webhooks.

    The payload shape is identical between 飞书 and its international version.
    """

    def __init__(
        self,
        url: str,
        *,
        title_prefix: str = "",
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ):
        self._url = url
        self._title_prefix = title_prefix
        self._http = http_client
        self._timeout = timeout

    async def send(self, *, title: str, body: str, level: str) -> None:
        full_title = f"{self._title_prefix}{title}" if self._title_prefix else title
        template = _LEVEL_TO_FEISHU_TEMPLATE.get(level, "blue")
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": full_title},
                    "template": template,
                },
                "elements": [{"tag": "markdown", "content": body}],
            },
        }
        client = self._http or httpx.AsyncClient(timeout=self._timeout)
        try:
            resp = await client.post(self._url, json=payload)
            resp.raise_for_status()
        finally:
            if self._http is None:
                await client.aclose()


class FeishuWebhookProvider(_WebhookCardProvider):
    """飞书 open.feishu.cn webhook bot."""


class LarkWebhookProvider(_WebhookCardProvider):
    """Lark (international) webhook bot — same card format as Feishu."""


class SmtpProvider(NotifyProvider):
    """Send email via SMTP. Password must come from an environment variable."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        from_addr: str,
        to_addrs: list[str],
        auth_env: str,
        use_tls: bool = True,
        smtp_factory: Callable[[str, int], Any] | None = None,
    ):
        self._host = host
        self._port = port
        self._from = from_addr
        self._to = to_addrs
        self._auth_env = auth_env
        self._use_tls = use_tls
        self._smtp_factory = smtp_factory or smtplib.SMTP

    async def send(self, *, title: str, body: str, level: str) -> None:
        password = os.environ.get(self._auth_env)
        if not password:
            raise RuntimeError(
                f"SMTP password env var {self._auth_env!r} is not set"
            )
        msg = MIMEMultipart("alternative")
        msg["Subject"] = title
        msg["From"] = self._from
        msg["To"] = ", ".join(self._to)
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with self._smtp_factory(self._host, self._port) as smtp:
            if self._use_tls:
                smtp.starttls()
            smtp.login(self._from, password)
            smtp.sendmail(self._from, self._to, msg.as_string())


def build_provider(channel_cfg: dict) -> NotifyProvider:
    """Instantiate a provider from a single channel's YAML config."""
    provider = channel_cfg.get("provider")

    if provider in ("feishu_webhook", "lark_webhook"):
        url = _expand_env(str(channel_cfg.get("url", "")))
        if not url:
            raise ValueError(f"channel {provider}: missing url")
        prefix = channel_cfg.get("default_title_prefix", "")
        cls = FeishuWebhookProvider if provider == "feishu_webhook" else LarkWebhookProvider
        return cls(url=url, title_prefix=prefix)

    if provider == "smtp":
        missing = [k for k in ("host", "from", "to", "auth_env") if k not in channel_cfg]
        if missing:
            raise ValueError(f"smtp channel missing fields: {missing}")
        to = channel_cfg["to"]
        if not isinstance(to, list):
            to = [to]
        return SmtpProvider(
            host=channel_cfg["host"],
            port=int(channel_cfg.get("port", 587)),
            from_addr=channel_cfg["from"],
            to_addrs=list(to),
            auth_env=channel_cfg["auth_env"],
            use_tls=bool(channel_cfg.get("use_tls", True)),
        )

    raise ValueError(f"unknown notify provider: {provider!r}")


class NotifyTool(Tool):
    """Send a notification through a configured channel.

    Channels are declared in ``.prax/notify.yaml``. The tool intentionally
    offers no ad-hoc URL input: every destination must go through a named
    channel so that credentials live in one place and permission review can
    reason about where messages go.
    """

    name = "Notify"
    description = (
        "Send a notification through a channel declared in .prax/notify.yaml "
        "(feishu_webhook / lark_webhook / smtp). Use this at the end of a "
        "long-running task, from a cron job, or whenever the user needs to be "
        "told the result without opening a terminal."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "channel": {
                "type": "string",
                "description": "Name of a channel defined in notify.yaml.",
            },
            "title": {
                "type": "string",
                "description": "Short headline — one line.",
            },
            "body": {
                "type": "string",
                "description": "Body, markdown supported for webhook providers.",
            },
            "level": {
                "type": "string",
                "enum": ["info", "warn", "error"],
                "default": "info",
                "description": "Severity tag used by providers for color/priority.",
            },
        },
        "required": ["channel", "title", "body"],
        "additionalProperties": False,
    }
    permission_level = PermissionLevel.REVIEW

    def __init__(
        self,
        *,
        cwd: str,
        provider_factory: Callable[[dict], NotifyProvider] | None = None,
    ):
        self._cwd = cwd
        self._provider_factory = provider_factory or build_provider

    async def execute(self, params: dict[str, Any]) -> ToolResult:
        from ..core.config_files import load_notify_config

        config = load_notify_config(self._cwd)
        channels = config.get("channels", {})
        name = params["channel"]
        cfg = channels.get(name)
        if not cfg:
            available = ", ".join(sorted(channels)) or "(none)"
            return ToolResult(
                content=(
                    f"Error: channel {name!r} not found. "
                    f"Available channels: {available}. "
                    f"Declare channels in .prax/notify.yaml."
                ),
                is_error=True,
            )

        try:
            provider = self._provider_factory(cfg)
        except Exception as e:
            return ToolResult(content=f"Error building provider: {e}", is_error=True)

        try:
            await provider.send(
                title=params["title"],
                body=params["body"],
                level=params.get("level", "info"),
            )
        except Exception as e:
            logger.warning("Notify send failed on channel %s: %s", name, e)
            return ToolResult(content=f"Error sending: {e}", is_error=True)

        return ToolResult(content=f"Notified channel {name!r}")
