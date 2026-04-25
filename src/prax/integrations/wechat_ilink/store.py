"""Persistent account storage for iLink bot credentials.

Each successful ``prax wechat login`` writes one JSON file under
``~/.prax/wechat/<account_id>.json`` (mode ``0o600``). The file holds the
bot token, the iLink server's per-account base URL, and the user's own
``user_id`` so that ``to: self`` in ``notify.yaml`` resolves correctly.

This module deliberately mirrors the layout used by hermes-agent's
``save_weixin_account`` so credentials are debuggable across the two
projects, but the directory root is Prax-owned.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from ...core.persistence import atomic_write_json


def wechat_dir(prax_home: Path | None = None) -> Path:
    """Return the directory that holds iLink account JSONs.

    ``prax_home`` defaults to ``~/.prax``; the override exists so tests can
    point this at a tmp_path.
    """
    root = prax_home if prax_home is not None else Path.home() / ".prax"
    path = root / "wechat"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class AccountRecord:
    account_id: str
    token: str
    base_url: str
    user_id: str
    saved_at: str


def save_account(
    *,
    account_id: str,
    token: str,
    base_url: str,
    user_id: str = "",
    prax_home: Path | None = None,
) -> AccountRecord:
    """Atomically persist (or update) an account record."""
    record = AccountRecord(
        account_id=account_id,
        token=token,
        base_url=base_url,
        user_id=user_id,
        saved_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )
    path = wechat_dir(prax_home) / f"{account_id}.json"
    atomic_write_json(path, asdict(record))
    try:
        path.chmod(0o600)
    except OSError:
        # Windows / non-POSIX: best-effort, no failure
        pass
    return record


def load_account(account_id: str, *, prax_home: Path | None = None) -> AccountRecord | None:
    path = wechat_dir(prax_home) / f"{account_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    allowed = {f for f in AccountRecord.__dataclass_fields__}
    filtered = {k: v for k, v in data.items() if k in allowed}
    try:
        return AccountRecord(**filtered)
    except TypeError:
        return None


def list_accounts(*, prax_home: Path | None = None) -> list[AccountRecord]:
    out: list[AccountRecord] = []
    for path in sorted(wechat_dir(prax_home).glob("*.json")):
        # Skip context-token caches if they ever appear next to credentials.
        if path.name.endswith(".context-tokens.json"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        allowed = {f for f in AccountRecord.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in allowed}
        try:
            out.append(AccountRecord(**filtered))
        except TypeError:
            continue
    return out


def delete_account(account_id: str, *, prax_home: Path | None = None) -> bool:
    """Remove a saved account. Returns True if a file was deleted."""
    path = wechat_dir(prax_home) / f"{account_id}.json"
    if not path.exists():
        return False
    try:
        os.unlink(path)
    except OSError:
        return False
    return True
