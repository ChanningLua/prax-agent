"""Claude Code integration lifecycle for Prax."""

from .backups import list_claude_backups
from .doctor import doctor_claude_install
from .history import list_claude_history
from .installer import install_claude_integration
from .list_archives import list_claude_archives
from .list_installed import list_installed_claude_assets
from .package_bundle import export_claude_marketplace_bundle, export_claude_plugin_bundle
from .repair import repair_claude_integration
from .restore import restore_claude_backup
from .show_state import show_claude_state
from .uninstall import uninstall_claude_integration

__all__ = [
    "doctor_claude_install",
    "export_claude_marketplace_bundle",
    "export_claude_plugin_bundle",
    "install_claude_integration",
    "list_claude_archives",
    "list_claude_backups",
    "list_claude_history",
    "list_installed_claude_assets",
    "repair_claude_integration",
    "restore_claude_backup",
    "show_claude_state",
    "uninstall_claude_integration",
]
