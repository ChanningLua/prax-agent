"""Read-only Codex project-skill discovery in an isolated macOS process.

This checks the installed CLI's discovery behavior, not activation in the
user's current Codex task. The caller must still match exact skill paths,
``scope == 'repo'``, enablement and the installed asset receipt.
"""
from __future__ import annotations

import json
import math
import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def _sandbox_available() -> bool:
    return (sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file()
            and os.access("/usr/bin/sandbox-exec", os.X_OK))


def _find_codex(codex_bin: str | None) -> str | None:
    if codex_bin:
        candidate = shutil.which(str(codex_bin))
        return str(Path(candidate).resolve()) if candidate else None
    candidate = shutil.which("codex")
    if candidate:
        return str(Path(candidate).resolve())
    for candidate in (
        "/Applications/ChatGPT.app/Contents/Resources/codex",
        "/Applications/Codex.app/Contents/Resources/codex",
    ):
        if Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _sandbox_profile(temporary: Path) -> str:
    escaped = str(temporary).replace("\\", "\\\\").replace('"', '\\"')
    return ('(version 1)(allow default)(deny network*)(deny file-write*)'
            f'(allow file-write* (subpath "{escaped}") (subpath "/dev"))')


def _stop_process_group(proc: subprocess.Popen) -> None:
    # No worker was started, so this metadata-only process needs no graceful
    # mutation flush. Kill the whole private group, including any descendants.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait(timeout=2)


class _Rpc:
    def __init__(self, proc: subprocess.Popen, deadline: float):
        self.proc = proc
        self.deadline = deadline
        self.buffer = b""
        self.total_bytes = 0
        self.selector = selectors.DefaultSelector()
        self.selector.register(proc.stdout, selectors.EVENT_READ)

    def close(self) -> None:
        self.selector.close()

    def send(self, value: dict) -> None:
        self.proc.stdin.write(json.dumps(value).encode("utf-8") + b"\n")
        self.proc.stdin.flush()

    def call(self, identifier: int, method: str, params: dict) -> dict:
        self.send({"id": identifier, "method": method, "params": params})
        while True:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Codex {method} timed out")
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                response = json.loads(line)
                if not isinstance(response, dict) or response.get("id") != identifier:
                    continue
                if "error" in response:
                    raise ValueError(f"Codex {method}: {response['error']}")
                result = response.get("result")
                if not isinstance(result, dict):
                    raise ValueError(f"Codex {method} returned an invalid result")
                return result
            if not self.selector.select(timeout=min(remaining, 0.1)):
                continue
            chunk = os.read(self.proc.stdout.fileno(), 65536)
            if not chunk:
                raise ValueError(f"Codex app-server exited before replying to {method}")
            self.total_bytes += len(chunk)
            if self.total_bytes > _MAX_RESPONSE_BYTES:
                raise ValueError("Codex discovery response exceeded its size limit")
            self.buffer += chunk


def probe_project_skills(
    project_root: Path, *, codex_bin: str | None = None, timeout_seconds: float = 15,
) -> dict:
    """Discover project skills without touching real user configuration.

    ``verified`` means the isolated discovery RPC completed without errors;
    it does not mean a specific Prax skill or the current task is activated.
    Temporary trust exposes existing project config only inside a disposable
    CODEX_HOME. Some CLI versions omit project enablement overrides from the
    discovery metadata; callers must independently respect explicit disables.
    Real user trust is never changed.
    Unsupported hosts fail closed instead of running an unconfined fallback.
    """
    result = {
        "verified": False, "activation_verified": False, "skills": [], "errors": [],
        "codex_version": None, "method": "isolated_discovery",
        "isolation": {
            "home": "temporary", "codex_home": "temporary", "network": "denied",
            "writes": "temporary directory and /dev only",
            "project_config": "read using trust in temporary CODEX_HOME only",
            "real_user_config_modified": False,
            "limitations": (
                "Does not establish activation in the current Codex task or real user profile. "
                "CLI discovery metadata can omit project skill-disable overrides; check them independently."
            ),
        },
    }
    if not _sandbox_available():
        result["errors"].append("An available macOS sandbox-exec is required; unconfined probing is disabled.")
        return result
    root = Path(project_root).resolve()
    if not root.is_dir():
        result["errors"].append("Project root must be an existing directory.")
        return result
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        result["errors"].append("Probe timeout must be a finite positive number.")
        return result
    codex = _find_codex(codex_bin)
    if codex is None:
        result["errors"].append("Codex CLI was not found; no discovery process was started.")
        return result
    deadline = time.monotonic() + timeout_seconds
    try:
        with tempfile.TemporaryDirectory(prefix="prax-codex-probe-") as temp:
            temporary = Path(temp).resolve()
            home, codex_home = temporary / "home", temporary / "codex"
            home.mkdir()
            codex_home.mkdir()
            for name in ("tmp", "config", "cache", "state", "data"):
                (temporary / name).mkdir()
            # This file is not the user's CODEX_HOME. The sandbox denies writes
            # to the real project and every location outside this temporary tree.
            (codex_home / "config.toml").write_text(
                'cli_auth_credentials_store = "file"\n'
                '[analytics]\nenabled = false\n'
                f'[projects.{json.dumps(str(root), ensure_ascii=False)}]\ntrust_level = "trusted"\n',
                encoding="utf-8",
            )
            environment = {
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(home),
                "CODEX_HOME": str(codex_home), "TMPDIR": str(temporary / "tmp"),
                "XDG_CONFIG_HOME": str(temporary / "config"),
                "XDG_CACHE_HOME": str(temporary / "cache"),
                "XDG_STATE_HOME": str(temporary / "state"),
                "XDG_DATA_HOME": str(temporary / "data"),
            }
            with (temporary / "stderr.log").open("w+b") as stderr:
                proc = subprocess.Popen(
                    ["/usr/bin/sandbox-exec", "-p", _sandbox_profile(temporary), codex,
                     "-C", str(root), "app-server", "--stdio", "--strict-config"],
                    cwd=root, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=stderr, start_new_session=True,
                )
                rpc = None
                try:
                    rpc = _Rpc(proc, deadline)
                    initialized = rpc.call(1, "initialize", {
                        "clientInfo": {"name": "prax_project_probe", "version": "1.0.0"},
                    })
                    user_agent = initialized.get("userAgent", "")
                    if not isinstance(user_agent, str):
                        raise ValueError("Codex initialize returned an invalid user agent")
                    version = re.match(r"prax_project_probe/([^ ]+)", user_agent)
                    result["codex_version"] = version.group(1) if version else None
                    rpc.send({"method": "initialized", "params": {}})
                    discovered = rpc.call(2, "skills/list", {"cwds": [str(root)], "forceReload": True})
                    entries = discovered.get("data")
                    if not isinstance(entries, list):
                        raise ValueError("Codex skills/list returned no discovery data")
                    entry = next((entry for entry in entries if isinstance(entry, dict)
                                  and entry.get("cwd") == str(root)), None)
                    if entry is None or not isinstance(entry.get("skills"), list):
                        raise ValueError("Codex skills/list did not return the requested project")
                    if not all(isinstance(skill, dict) for skill in entry["skills"]):
                        raise ValueError("Codex skills/list returned malformed skill metadata")
                    result["skills"] = entry["skills"]
                    errors = entry.get("errors", [])
                    if not isinstance(errors, list):
                        raise ValueError("Codex skills/list returned malformed discovery errors")
                    result["errors"].extend(str(error) for error in errors)
                    result["verified"] = not result["errors"]
                except (OSError, ValueError, TimeoutError) as exc:
                    result["errors"].append(str(exc))
                finally:
                    if rpc is not None:
                        rpc.close()
                    try:
                        _stop_process_group(proc)
                    finally:
                        proc.stdin.close()
                        proc.stdout.close()
                if not result["verified"]:
                    stderr.seek(0, os.SEEK_END)
                    stderr.seek(max(0, stderr.tell() - 4000))
                    detail = stderr.read().decode("utf-8", errors="replace").strip()
                    if detail:
                        result["errors"].append(detail)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        result["verified"] = False
        result["errors"].append(str(exc))
    return result
