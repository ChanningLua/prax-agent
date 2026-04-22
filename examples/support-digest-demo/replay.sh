#!/usr/bin/env bash
# Rebuild the support-digest demo: drop the 8 sample tickets (shipped with
# the skill) into ./sandbox/.prax/inbox/tickets-2026-04-21.json and clear any
# prior digest output. Idempotent.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEMO="$SCRIPT_DIR/sandbox"
SRC="$REPO_ROOT/docs/recipes/support-digest/sample-tickets.json"
DATE=2026-04-21

if [[ ! -f "$SRC" ]]; then
    echo "FATAL: sample tickets file missing: $SRC" >&2
    exit 1
fi

rm -rf "$DEMO"
mkdir -p "$DEMO/.prax/inbox"
cp "$SRC" "$DEMO/.prax/inbox/tickets-${DATE}.json"

echo "Sandbox rebuilt at: $DEMO"
echo "  .prax/inbox/tickets-${DATE}.json   (8 fictional tickets with PII)"
echo
echo "Next:"
echo "  export OPENAI_API_KEY=..."
echo "  cd $DEMO"
echo "  prax prompt --permission-mode workspace-write \\"
echo "    \"触发 support-digest 技能，处理 .prax/inbox/tickets-${DATE}.json\""
echo "  cd $SCRIPT_DIR && ./assertions.sh"
