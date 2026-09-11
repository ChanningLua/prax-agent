"""Contract-backed Prax Shift; execution belongs to the shared harness kernel."""
from __future__ import annotations


def main(argv=None) -> int:
    from ..repository_harness import main as harness_main

    args = list(argv or [])
    # The short spelling starts one task; its id owns subsequent continuation.
    if args and args[0] == "--task":
        args.insert(0, "run")
    return harness_main(args, prog="prax shift")
