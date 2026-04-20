from __future__ import annotations

import asyncio
import argparse
import json
from pathlib import Path

from prax.commands.handlers import CommandContext, run_command
from prax.commands.registry import ParsedCommand
from prax.core.config_files import load_models_config
from prax.core.permissions import PermissionMode
from prax.core.runtime_env import hydrate_runtime_env
from prax.core.runtime_paths import RUNTIME_NATIVE
from prax.core.session_store import FileSessionStore

from .integrations.claude_code import (
    doctor_claude_install,
    export_claude_marketplace_bundle,
    export_claude_plugin_bundle,
    list_claude_archives,
    install_claude_integration,
    list_claude_backups,
    list_claude_history,
    list_installed_claude_assets,
    repair_claude_integration,
    restore_claude_backup,
    show_claude_state,
    uninstall_claude_integration,
)
from .runtime import NativeRuntime


def _command_context(cwd: str) -> CommandContext:
    models_config = load_models_config(cwd)
    hydrate_runtime_env(models_config, cwd)
    return CommandContext(
        cwd=cwd,
        models_config=models_config,
        session_store=FileSessionStore(str(Path(cwd) / ".prax" / "sessions")),
    )


def _render(payload: dict, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    print(payload.get("text") or json.dumps(payload, indent=2, ensure_ascii=False))


def _add_shared_json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prax")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prompt = subparsers.add_parser("prompt", help="Run a task via the Prax native runtime")
    prompt.add_argument("task", nargs="+")
    prompt.add_argument("--model")
    prompt.add_argument("--session-id")
    prompt.add_argument("--permission-mode")

    providers = subparsers.add_parser("providers", help="Show configured providers")
    _add_shared_json_flag(providers)

    status = subparsers.add_parser("status", help="Show Prax runtime and Claude integration status")
    _add_shared_json_flag(status)

    for name, help_text in (
        ("install", "Install Prax Claude Code assets"),
        ("doctor", "Diagnose Prax Claude Code integration"),
        ("list-installed", "List Prax-managed Claude Code assets"),
        ("show-state", "Show current or archived Claude integration state"),
        ("history", "Show Claude integration lifecycle history"),
        ("list-archives", "List archived Claude integration states"),
        ("list-backups", "List Claude integration backups"),
        ("repair", "Repair Prax Claude Code integration drift"),
        ("export-plugin", "Export a publishable Claude plugin bundle"),
        ("export-marketplace", "Export a publishable Claude marketplace bundle"),
        ("restore", "Restore managed Claude settings or MCP backup"),
        ("uninstall", "Remove Prax Claude Code assets"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        if name not in {"export-plugin", "export-marketplace"}:
            sub.add_argument("--target", default="claude")
            sub.add_argument("--root")
        _add_shared_json_flag(sub)
        if name in {"install", "repair", "restore", "uninstall"}:
            sub.add_argument("--dry-run", action="store_true")
        if name in {"install", "repair"}:
            sub.add_argument("--plugin-repo")
            sub.add_argument("--profile", default="full")
        if name in {"export-plugin", "export-marketplace"}:
            sub.add_argument("--out-dir", required=True)
            sub.add_argument("--profile", default="full")
        if name == "restore":
            sub.add_argument("--artifact", required=True)
            sub.add_argument("--backup-name")
            sub.add_argument("--backup-path")
        if name == "show-state":
            sub.add_argument("--archived-name")

    debug = subparsers.add_parser("debug-claude", help="Run the legacy Claude subprocess bridge explicitly")
    debug.add_argument("task", nargs="+")
    debug.add_argument("--model")
    debug.add_argument("--session-id")
    debug.add_argument("--permission-mode")

    return parser


def _ensure_claude_target(target: str) -> None:
    if target != "claude":
        raise ValueError("Only --target claude is currently supported")


def _status_payload(cwd: str) -> dict:
    ctx = _command_context(cwd)
    status_result = run_command(ParsedCommand(name="status", args=[]), ctx)
    providers_result = run_command(ParsedCommand(name="providers", args=[]), ctx)
    claude_result = doctor_claude_install()
    claude_inventory = list_installed_claude_assets()
    runtime_data = dict(status_result.data or {})
    runtime_data.setdefault("runtime_path", RUNTIME_NATIVE)
    runtime_data.setdefault("integration_mode", "native")
    runtime_data.setdefault("executor", "direct-api")
    return {
        "runtime": runtime_data,
        "providers": providers_result.data,
        "claude_integration": claude_result,
        "claude_inventory": claude_inventory,
        "text": (
            f"runtime_path={runtime_data.get('runtime_path')} "
            f"integration_mode={runtime_data.get('integration_mode')} "
            f"claude_status={claude_result.get('status')}"
        ),
    }


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    cwd = str(Path.cwd())

    if args.command == "prompt":
        permission_mode = None
        if args.permission_mode:
            permission_mode = PermissionMode(args.permission_mode)
        NativeRuntime().run_task_sync(
            " ".join(args.task),
            cwd=cwd,
            model_override=args.model,
            permission_mode=permission_mode,
            session_id=args.session_id,
        )
        return

    if args.command == "debug-claude":
        from prax.main import _run

        permission_mode = None
        if args.permission_mode:
            permission_mode = PermissionMode(args.permission_mode)
        asyncio.run(
            _run(
                " ".join(args.task),
                model_override=args.model,
                permission_mode=permission_mode,
                session_id=args.session_id,
            )
        )
        return

    if args.command == "providers":
        ctx = _command_context(cwd)
        result = run_command(ParsedCommand(name="providers", args=[]), ctx)
        _render({"text": result.text, "providers": result.data["providers"]}, as_json=args.json)
        return

    if args.command == "status":
        _render(_status_payload(cwd), as_json=args.json)
        return

    if args.command == "export-plugin":
        _render(
            export_claude_plugin_bundle(out_dir=args.out_dir, profile=args.profile),
            as_json=args.json,
        )
        return
    if args.command == "export-marketplace":
        _render(
            export_claude_marketplace_bundle(out_dir=args.out_dir, profile=args.profile),
            as_json=args.json,
        )
        return

    _ensure_claude_target(args.target)
    if args.command == "install":
        _render(
            install_claude_integration(
                target_root=args.root,
                dry_run=args.dry_run,
                plugin_repo=args.plugin_repo,
                profile=args.profile,
            ),
            as_json=args.json,
        )
        return
    if args.command == "doctor":
        _render(doctor_claude_install(target_root=args.root), as_json=args.json)
        return
    if args.command == "list-installed":
        _render(list_installed_claude_assets(target_root=args.root), as_json=args.json)
        return
    if args.command == "show-state":
        _render(show_claude_state(target_root=args.root, archived_name=args.archived_name), as_json=args.json)
        return
    if args.command == "history":
        _render(list_claude_history(target_root=args.root), as_json=args.json)
        return
    if args.command == "list-archives":
        _render(list_claude_archives(target_root=args.root), as_json=args.json)
        return
    if args.command == "list-backups":
        _render(list_claude_backups(target_root=args.root), as_json=args.json)
        return
    if args.command == "repair":
        _render(
            repair_claude_integration(
                target_root=args.root,
                dry_run=args.dry_run,
                plugin_repo=args.plugin_repo,
                profile=args.profile,
            ),
            as_json=args.json,
        )
        return
    if args.command == "restore":
        _render(
            restore_claude_backup(
                target_root=args.root,
                artifact=args.artifact,
                backup_name=args.backup_name,
                backup_path=args.backup_path,
                dry_run=args.dry_run,
            ),
            as_json=args.json,
        )
        return
    if args.command == "uninstall":
        _render(uninstall_claude_integration(target_root=args.root, dry_run=args.dry_run), as_json=args.json)
        return

    raise SystemExit(1)


if __name__ == "__main__":
    main()
