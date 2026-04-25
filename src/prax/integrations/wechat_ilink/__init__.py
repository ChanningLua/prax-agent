"""WeChat personal-account integration via Tencent's iLink Bot API.

Adapted from hermes-agent's ``gateway/platforms/weixin.py``
(MIT, 2025 Nous Research) — only the push-only subset is reproduced here:
QR login + token persistence + outbound text send. Inbound long-poll,
media encryption, context-token expiry handling, and the full bidirectional
adapter intentionally stay in Hermes; if Prax ever needs them, the pattern
is documented there.
"""

from .client import (
    ILINK_BASE_URL,
    QrLoginResult,
    qr_login,
    send_text,
)
from .store import (
    AccountRecord,
    delete_account,
    list_accounts,
    load_account,
    save_account,
)

__all__ = [
    "ILINK_BASE_URL",
    "QrLoginResult",
    "qr_login",
    "send_text",
    "AccountRecord",
    "delete_account",
    "list_accounts",
    "load_account",
    "save_account",
]
