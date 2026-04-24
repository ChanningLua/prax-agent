from __future__ import annotations

from .archive import list_archived_states, read_archived_state
from .state import default_target_root, load_install_state


def show_claude_state(*, target_root: str | None = None, archived_name: str | None = None) -> dict:
    root = default_target_root(target_root)

    if archived_name is not None or not load_install_state(root):
        archived = read_archived_state(root, name=archived_name)
        if archived is None:
            return {
                "target": "claude",
                "target_root": str(root),
                "state": None,
                "text": f"Prax Claude state\n"
                f"target_root: {root}\n"
                "state: none",
            }
        return {
            "target": "claude",
            "target_root": str(root),
            "state": archived,
            "text": (
                "Prax Claude archived state\n"
                f"target_root: {root}\n"
                f"profile: {archived.get('profile')}\n"
                f"archive: {archived.get('_archive', {}).get('name')}\n"
                f"assets: {len(archived.get('assets', []))}"
            ),
        }

    state = load_install_state(root)
    assert state is not None
    return {
        "target": "claude",
        "target_root": str(root),
        "state": {
            "schema_version": state.schema_version,
            "installed_at": state.installed_at,
            "last_validated_at": state.last_validated_at,
            "profile": state.profile,
            "asset_count": len(state.assets),
            "history_count": len(state.history),
            "backups": state.backups,
            "archives": list_archived_states(root),
        },
        "text": (
            "Prax Claude state\n"
            f"target_root: {root}\n"
            f"profile: {state.profile}\n"
            f"assets: {len(state.assets)}\n"
            f"history: {len(state.history)}"
        ),
    }
