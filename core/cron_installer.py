"""Install / uninstall the Prax cron dispatcher on the host OS.

The installer only arranges for ``prax cron run`` to be invoked once a minute.
Individual jobs live in ``.prax/cron.yaml`` and are evaluated by the
dispatcher at tick time, so schedule changes never need a re-install.

- macOS: writes a LaunchAgent plist with ``StartInterval=60`` and loads it
  via ``launchctl``.
- Linux: emits a ``crontab`` line the user can paste via ``crontab -e`` (we
  don't rewrite crontabs automatically — too destructive).
- Windows: raises ``NotImplementedError`` with guidance.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path


_DISPATCHER_LABEL = "dev.prax.cron.dispatcher"


def _launchagents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _default_prax_argv() -> list[str]:
    """Absolute argv for the dispatcher — avoids PATH surprises under launchd."""
    return [sys.executable, "-m", "prax", "cron", "run"]


# ── Plist helpers ───────────────────────────────────────────────────────────


def build_macos_plist(
    *,
    label: str,
    cwd: str,
    prax_argv: list[str],
    log_dir: str,
) -> str:
    """Render a LaunchAgent plist as an XML string."""
    if len(prax_argv) == 0:
        raise ValueError("prax_argv must not be empty")
    # Ensure argv carries the `cron run` suffix even if the caller supplied only a binary.
    if prax_argv[-2:] != ["cron", "run"]:
        prax_argv = list(prax_argv) + ["cron", "run"]

    payload = {
        "Label": label,
        "ProgramArguments": list(prax_argv),
        "WorkingDirectory": cwd,
        "StartInterval": 60,
        "RunAtLoad": False,
        "StandardOutPath": str(Path(log_dir) / "dispatcher.stdout.log"),
        "StandardErrorPath": str(Path(log_dir) / "dispatcher.stderr.log"),
        "EnvironmentVariables": {
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        },
    }
    return plistlib.dumps(payload).decode("utf-8")


def install_macos(*, cwd: str, prax_argv: list[str] | None = None) -> dict:
    argv = prax_argv or _default_prax_argv()
    if argv[-2:] != ["cron", "run"]:
        argv = list(argv) + ["cron", "run"]

    launchagents = _launchagents_dir()
    launchagents.mkdir(parents=True, exist_ok=True)
    plist_path = launchagents / f"{_DISPATCHER_LABEL}.plist"

    log_dir = Path(cwd) / ".prax" / "logs" / "cron"
    plist_xml = build_macos_plist(
        label=_DISPATCHER_LABEL,
        cwd=cwd,
        prax_argv=argv,
        log_dir=str(log_dir),
    )
    plist_path.write_text(plist_xml, encoding="utf-8")

    # Try bootout first (ignore errors) so a stale job is removed before load.
    subprocess.run(
        ["launchctl", "unload", str(plist_path)],
        check=False, capture_output=True, text=True,
    )
    load = subprocess.run(
        ["launchctl", "load", "-w", str(plist_path)],
        check=False, capture_output=True, text=True,
    )

    return {
        "plist_path": str(plist_path),
        "label": _DISPATCHER_LABEL,
        "launchctl_load_returncode": load.returncode,
        "launchctl_load_stderr": load.stderr,
    }


def uninstall_macos(*, cwd: str) -> dict:
    plist_path = _launchagents_dir() / f"{_DISPATCHER_LABEL}.plist"

    # Always try to unload, even if the file is already gone.
    subprocess.run(
        ["launchctl", "unload", str(plist_path)],
        check=False, capture_output=True, text=True,
    )

    if plist_path.exists():
        plist_path.unlink()
        return {"uninstalled": True, "plist_path": str(plist_path)}
    return {"uninstalled": False, "plist_path": str(plist_path)}


# ── Linux crontab (no auto-rewrite) ─────────────────────────────────────────


def build_linux_crontab_line(*, cwd: str, prax_argv: list[str] | None = None) -> str:
    argv = prax_argv or [sys.executable, "-m", "prax"]
    # Normalize: strip any trailing cron run the caller already put in.
    base = list(argv)
    if base[-2:] == ["cron", "run"]:
        base = base[:-2]
    cmd = " ".join(base + ["cron", "run"])
    return f"* * * * * cd {cwd} && {cmd}  # prax-dispatcher"


def install_linux(*, cwd: str, prax_argv: list[str] | None = None) -> dict:
    line = build_linux_crontab_line(cwd=cwd, prax_argv=prax_argv)
    return {
        "crontab_line": line,
        "instructions": (
            "Run `crontab -e` and append the following line (or pipe into your "
            "crontab tool of choice). Prax deliberately does not edit your "
            "crontab automatically:\n\n    " + line
        ),
    }


# ── Platform dispatch ───────────────────────────────────────────────────────


def install(*, cwd: str, prax_argv: list[str] | None = None) -> dict:
    if sys.platform == "darwin":
        return install_macos(cwd=cwd, prax_argv=prax_argv)
    if sys.platform.startswith("linux"):
        return install_linux(cwd=cwd, prax_argv=prax_argv)
    raise NotImplementedError(
        f"Prax cron install is not supported on {sys.platform!r} yet. "
        "Windows users: run `prax cron run` every minute via Task Scheduler."
    )


def uninstall(*, cwd: str) -> dict:
    if sys.platform == "darwin":
        return uninstall_macos(cwd=cwd)
    if sys.platform.startswith("linux"):
        line = build_linux_crontab_line(cwd=cwd)
        return {
            "crontab_line": line,
            "instructions": (
                "Run `crontab -e` and remove any line matching:\n\n    " + line
            ),
        }
    raise NotImplementedError(f"Prax cron uninstall is not supported on {sys.platform!r}")
