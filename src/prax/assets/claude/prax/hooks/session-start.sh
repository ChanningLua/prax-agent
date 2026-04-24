#!/bin/sh

if command -v prax >/dev/null 2>&1; then
  prax doctor --target claude --json >/dev/null 2>&1 || true
elif command -v python3 >/dev/null 2>&1; then
  python3 -m prax.cli doctor --target claude --json >/dev/null 2>&1 || true
fi

exit 0
