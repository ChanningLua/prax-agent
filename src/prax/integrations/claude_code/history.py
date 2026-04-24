from __future__ import annotations

from .state import default_target_root, load_install_state


def list_claude_history(*, target_root: str | None = None) -> dict:
    root = default_target_root(target_root)
    state = load_install_state(root)
    if state is None:
        return {
            "target": "claude",
            "target_root": str(root),
            "installed": False,
            "history": [],
            "text": f"Prax Claude history\n"
            f"target_root: {root}\n"
            "installed: False\n"
            "history: none",
        }

    history = [
        {
            "operation": entry.operation,
            "timestamp": entry.timestamp,
            "details": entry.details,
        }
        for entry in state.history
    ]
    lines = [
        "Prax Claude history",
        f"target_root: {root}",
        f"installed: True",
        f"entries: {len(history)}",
    ]
    if history:
        lines.append("history:")
        for entry in history[-10:]:
            lines.append(f"  - {entry['timestamp']} {entry['operation']} {entry['details']}")
    else:
        lines.append("history: none")

    return {
        "target": "claude",
        "target_root": str(root),
        "installed": True,
        "history": history,
        "text": "\n".join(lines),
    }
