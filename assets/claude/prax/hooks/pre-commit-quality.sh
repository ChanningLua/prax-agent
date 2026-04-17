#!/usr/bin/env bash
# prax PreToolUse hook: commit quality gate
# Intercepts git commit commands, enforces quality checks before committing.
# Exit 0 = allow, Exit 2 = block

set -euo pipefail

# Read tool input from stdin
INPUT=$(cat)
COMMAND=$(echo "$INPUT" | grep -o '"command":"[^"]*"' | head -1 | sed 's/"command":"//;s/"//')

# Only intercept git commit commands
if ! echo "$COMMAND" | grep -qE 'git\s+commit'; then
  exit 0
fi

# Block --no-verify flag
if echo "$COMMAND" | grep -qE '\-\-no-verify'; then
  echo '{"error":"Blocked: --no-verify bypasses safety checks. Remove the flag and fix hook issues instead."}'
  exit 2
fi

# Block empty commit messages
if echo "$COMMAND" | grep -qE '\-m\s+["'"'"']\s*["'"'"']'; then
  echo '{"error":"Blocked: Empty commit message. Provide a meaningful description."}'
  exit 2
fi

exit 0
