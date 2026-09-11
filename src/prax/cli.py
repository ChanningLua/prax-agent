from __future__ import annotations

import asyncio
import argparse
import json
import sys
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
from .integrations.codex import (
    doctor_codex_install,
    install_codex_integration,
    list_installed_codex_assets,
    uninstall_codex_integration,
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

    status = subparsers.add_parser(
        "status",
        help="Show Prax runtime and coding-agent integration status",
    )
    _add_shared_json_flag(status)

    for name, help_text in (
        ("install", "Install Prax coding-agent assets"),
        ("doctor", "Diagnose a Prax coding-agent integration"),
        ("list-installed", "List Prax-managed coding-agent assets"),
        ("show-state", "Show current or archived Claude integration state"),
        ("history", "Show Claude integration lifecycle history"),
        ("list-archives", "List archived Claude integration states"),
        ("list-backups", "List Claude integration backups"),
        ("repair", "Repair Prax Claude Code integration drift"),
        ("export-plugin", "Export a publishable Claude plugin bundle"),
        ("export-marketplace", "Export a publishable Claude marketplace bundle"),
        ("restore", "Restore managed Claude settings or MCP backup"),
        ("uninstall", "Remove Prax coding-agent assets"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        if name not in {"export-plugin", "export-marketplace"}:
            sub.add_argument("--target", default="claude")
            sub.add_argument("--root")
        if name in {"install", "doctor"}:
            scope = sub.add_mutually_exclusive_group()
            scope.add_argument("--local", action="store_true",
                               help="Codex only: install/diagnose the current project's repo skill")
            scope.add_argument("--global", dest="global_scope", action="store_true",
                               help="Codex only: explicitly select the user-level plugin")
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

    cron = subparsers.add_parser("cron", help="Manage scheduled jobs (see .prax/cron.yaml)")
    cron_sub = cron.add_subparsers(dest="cron_action", required=True)

    c_list = cron_sub.add_parser("list", help="List configured cron jobs")
    _add_shared_json_flag(c_list)

    c_add = cron_sub.add_parser("add", help="Add a new cron job")
    c_add.add_argument("--name", required=True)
    c_add.add_argument("--schedule", required=True,
                       help="5-field cron expression, e.g. '0 17 * * *'")
    c_add.add_argument("--prompt", default="",
                       help="Task prompt passed to `prax prompt` when the job fires")
    c_add.add_argument("--session-id")
    c_add.add_argument("--task", help="workspace-relative task contract for Shift; owns goal, verifier, and budget")
    c_add.add_argument("--model")
    c_add.add_argument(
        "--run-mode",
        choices=["prompt", "shift", "orchestrate"],
        default="prompt",
        help="use shift for a bounded, verifiable Prax Shift window",
    )
    c_add.add_argument(
        "--agent-host",
        choices=["auto", "claude", "codex"],
        help="coding-agent worker for orchestrate jobs",
    )
    c_add.add_argument("--notify-on", nargs="*", choices=["success", "failure"], default=[])
    c_add.add_argument("--notify-channel")
    _add_shared_json_flag(c_add)

    c_remove = cron_sub.add_parser("remove", help="Remove a cron job by name")
    c_remove.add_argument("--name", required=True)
    _add_shared_json_flag(c_remove)

    c_run = cron_sub.add_parser("run",
                                help="Dispatch all due jobs once (invoked by the system scheduler)")
    _add_shared_json_flag(c_run)

    c_install = cron_sub.add_parser(
        "install",
        help="Install the per-minute dispatcher (LaunchAgent on macOS, crontab line on Linux)",
    )
    _add_shared_json_flag(c_install)

    c_uninstall = cron_sub.add_parser("uninstall", help="Remove the dispatcher")
    _add_shared_json_flag(c_uninstall)

    wechat = subparsers.add_parser(
        "wechat",
        help="Manage personal-WeChat (iLink) credentials for the wechat_personal notify provider",
    )
    wechat_sub = wechat.add_subparsers(dest="wechat_action", required=True)

    w_login = wechat_sub.add_parser(
        "login",
        help="Run the QR-code login flow and persist credentials under ~/.prax/wechat/",
    )
    w_login.add_argument(
        "--bot-type",
        default="3",
        help="iLink bot_type parameter; defaults to '3' (matches Hermes)",
    )
    w_login.add_argument(
        "--timeout",
        type=int,
        default=480,
        help="Seconds to wait for the QR scan before giving up",
    )

    w_list = wechat_sub.add_parser("list", help="List saved iLink accounts")
    _add_shared_json_flag(w_list)

    w_send = wechat_sub.add_parser("send", help="Send a one-off test message to verify a saved account")
    w_send.add_argument("--account", required=True, help="account_id from `prax wechat list`")
    w_send.add_argument(
        "--to",
        default="self",
        help="Recipient user_id, or the literal 'self' to send to the logged-in account itself",
    )
    w_send.add_argument("text", nargs="+", help="Message text")

    w_logout = wechat_sub.add_parser(
        "logout", help="Delete a saved iLink account credential file"
    )
    w_logout.add_argument("--account", required=True)

    return parser


def _ensure_target_support(command: str, target: str) -> None:
    if target not in {"claude", "codex"}:
        raise ValueError("--target must be 'claude' or 'codex'")
    codex_commands = {"install", "doctor", "list-installed", "repair", "uninstall"}
    if target == "codex" and command not in codex_commands:
        raise ValueError(
            f"{command!r} is a Claude-only integration lifecycle command; "
            "Codex supports install, doctor, list-installed, repair, and uninstall"
        )


def _ensure_claude_target(target: str) -> None:
    """Backward-compatible guard retained for integration API consumers."""
    if target != "claude":
        raise ValueError("Only --target claude is supported by this operation")


def _codex_project_root(cwd: str) -> Path | None:
    """Use the nearest repository/installation, never a sibling or global plugin."""
    current = Path(cwd).resolve()
    for root in (current, *current.parents):
        if ((root / ".git").exists() or (root / ".prax/codex-install.json").exists()
                or (root / ".codex/skills/prax-shift").exists()):
            return root
    return None


def _status_payload(cwd: str) -> dict:
    ctx = _command_context(cwd)
    status_result = run_command(ParsedCommand(name="status", args=[]), ctx)
    providers_result = run_command(ParsedCommand(name="providers", args=[]), ctx)
    claude_result = doctor_claude_install()
    claude_inventory = list_installed_claude_assets()
    try:
        codex_result = doctor_codex_install()
        codex_inventory = list_installed_codex_assets()
    except (RuntimeError, ValueError) as exc:
        codex_result = {
            "target": "codex",
            "status": "unavailable",
            "error": str(exc),
        }
        codex_inventory = {
            "target": "codex",
            "assets": [],
            "error": str(exc),
        }
    runtime_data = dict(status_result.data or {})
    runtime_data.setdefault("runtime_path", RUNTIME_NATIVE)
    runtime_data.setdefault("integration_mode", "native")
    runtime_data.setdefault("executor", "direct-api")
    return {
        "runtime": runtime_data,
        "providers": providers_result.data,
        "claude_integration": claude_result,
        "claude_inventory": claude_inventory,
        "codex_integration": codex_result,
        "codex_inventory": codex_inventory,
        "text": (
            f"runtime_path={runtime_data.get('runtime_path')} "
            f"integration_mode={runtime_data.get('integration_mode')} "
            f"claude_status={claude_result.get('status')} "
            f"codex_status={codex_result.get('status')}"
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

    if args.command == "cron":
        from .commands import cron as cron_cmd
        from .core import cron_installer
        from .core.cron_store import (
            DuplicateJobError,
            InvalidScheduleError,
            UnknownJobError,
        )

        action = args.cron_action
        try:
            if action == "list":
                _render(cron_cmd.handle_list(cwd, as_json=args.json), as_json=args.json)
            elif action == "add":
                _render(
                    cron_cmd.handle_add(
                        cwd,
                        name=args.name,
                        schedule=args.schedule,
                        prompt=args.prompt,
                        session_id=args.session_id,
                        model=args.model,
                        notify_on=args.notify_on,
                        notify_channel=args.notify_channel,
                        # ``orchestrate`` remains the durable storage value so
                        # existing cron.yaml files stay compatible. ``shift`` is
                        # the product-facing spelling.
                        run_mode=(
                            "orchestrate"
                            if args.run_mode in {"shift", "orchestrate"}
                            else args.run_mode
                        ),
                        agent_host=args.agent_host,
                        task_path=args.task,
                    ),
                    as_json=args.json,
                )
            elif action == "remove":
                _render(cron_cmd.handle_remove(cwd, name=args.name), as_json=args.json)
            elif action == "run":
                _render(cron_cmd.handle_run(cwd, as_json=args.json), as_json=args.json)
            elif action == "install":
                _render(cron_installer.install(cwd=cwd), as_json=args.json)
            elif action == "uninstall":
                _render(cron_installer.uninstall(cwd=cwd), as_json=args.json)
        except (DuplicateJobError, UnknownJobError, InvalidScheduleError, ValueError) as e:
            print(f"Error: {e}", file=sys.stderr)
            raise SystemExit(1)
        except NotImplementedError as e:
            print(f"Error: {e}", file=sys.stderr)
            raise SystemExit(2)
        return

    if args.command == "wechat":
        from .integrations import wechat_ilink as wx

        action = args.wechat_action
        if action == "login":
            result = asyncio.run(
                wx.qr_login(bot_type=args.bot_type, timeout_seconds=args.timeout)
            )
            if result is None:
                raise SystemExit(1)
            return

        if action == "list":
            accounts = wx.list_accounts()
            if not accounts:
                _render(
                    {"text": "No saved iLink accounts. Run `prax wechat login` first.",
                     "accounts": []},
                    as_json=args.json,
                )
                return
            payload = {
                "accounts": [
                    {
                        "account_id": a.account_id,
                        "user_id": a.user_id,
                        "base_url": a.base_url,
                        "saved_at": a.saved_at,
                    }
                    for a in accounts
                ]
            }
            text_lines = [
                f"  {a.account_id}  user_id={a.user_id or '(none)'}  saved_at={a.saved_at}"
                for a in accounts
            ]
            payload["text"] = "Saved iLink accounts:\n" + "\n".join(text_lines)
            _render(payload, as_json=args.json)
            return

        if action == "send":
            account = wx.load_account(args.account)
            if account is None:
                print(
                    f"Error: account {args.account!r} not found. Run `prax wechat login` or `prax wechat list`.",
                    file=sys.stderr,
                )
                raise SystemExit(1)
            recipient = account.user_id if args.to == "self" else args.to
            if not recipient:
                print(
                    "Error: account has no user_id; specify an explicit --to <user_id>.",
                    file=sys.stderr,
                )
                raise SystemExit(1)
            text = " ".join(args.text)
            try:
                asyncio.run(wx.send_text(account, to_user_id=recipient, text=text))
            except Exception as exc:
                print(f"Error: send failed: {exc}", file=sys.stderr)
                raise SystemExit(1)
            print(f"OK — sent to {recipient}")
            return

        if action == "logout":
            removed = wx.delete_account(args.account)
            if not removed:
                print(f"No saved account named {args.account!r}.", file=sys.stderr)
                raise SystemExit(1)
            print(f"Deleted account {args.account!r}.")
            return

        raise SystemExit(1)

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

    try:
        _ensure_target_support(args.command, args.target)
        if args.target != "codex" and (getattr(args, "local", False)
                                       or getattr(args, "global_scope", False)):
            raise ValueError("--local/--global are supported only with --target codex")
        if getattr(args, "local", False) and args.root:
            raise ValueError("--local uses the current project; do not combine it with --root")
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)

    if args.target == "codex":
        if args.command == "install" and args.local:
            root = _codex_project_root(cwd) or Path(cwd).resolve()
            try:
                result = install_codex_integration(local=True, project_root=str(root),
                                                   dry_run=args.dry_run)
            except (OSError, ValueError, RuntimeError) as exc:
                result = {"target": "codex", "scope": "project", "status": "error",
                          "project_root": str(root), "error": str(exc), "text": str(exc)}
            _render(result, as_json=args.json)
            if result.get("status") not in {"ok", "planned"}:
                raise SystemExit(2)
            return
        if args.command in {"install", "repair"}:
            _render(
                install_codex_integration(
                    target_root=args.root,
                    dry_run=args.dry_run,
                ),
                as_json=args.json,
            )
            return
        if args.command == "doctor":
            root = None if args.global_scope or args.root else _codex_project_root(cwd)
            if args.local:
                root = root or Path(cwd).resolve()
            try:
                result = (doctor_codex_install(local=True, project_root=str(root)) if root else
                          doctor_codex_install(target_root=args.root, local=False))
            except (OSError, ValueError, RuntimeError) as exc:
                result = {"target": "codex", "scope": "project" if root else "user",
                          "status": "error", "error": str(exc), "text": str(exc)}
            _render(result, as_json=args.json)
            if root and result.get("status") != "ok":
                raise SystemExit(2)
            return
        if args.command == "list-installed":
            _render(
                list_installed_codex_assets(target_root=args.root),
                as_json=args.json,
            )
            return
        if args.command == "uninstall":
            _render(
                uninstall_codex_integration(
                    target_root=args.root,
                    dry_run=args.dry_run,
                ),
                as_json=args.json,
            )
            return

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
