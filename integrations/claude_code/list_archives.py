from __future__ import annotations

from .archive import list_archived_states
from .state import default_target_root


def list_claude_archives(*, target_root: str | None = None) -> dict:
    root = default_target_root(target_root)
    archives = list_archived_states(root)
    lines = [
        "Prax Claude archives",
        f"target_root: {root}",
        f"archive_count: {len(archives)}",
    ]
    if archives:
        lines.append("archives:")
        for entry in archives:
            lines.append(f"  - {entry['name']} size={entry['size']} path={entry['path']}")
    else:
        lines.append("archives: none")

    return {
        "target": "claude",
        "target_root": str(root),
        "archives": archives,
        "text": "\n".join(lines),
    }
