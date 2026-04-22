#!/usr/bin/env bash
# Rebuild the knowledge-compile demo: copy the 6 sample source markdowns
# (shipped with the ai-news-daily tutorial) into ./sandbox/, so the
# knowledge-compile skill has something to compile.
#
# Idempotent.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SRC="$REPO_ROOT/docs/tutorials/ai-news-daily/sample-vault"
DEMO="$SCRIPT_DIR/sandbox"
DATE=2026-04-21

if [[ ! -d "$SRC" ]]; then
    echo "FATAL: sample vault missing at $SRC" >&2
    exit 1
fi

rm -rf "$DEMO"
mkdir -p "$DEMO/.prax/vault/ai-news-hub/$DATE/raw"
cp "$SRC"/*.md "$DEMO/.prax/vault/ai-news-hub/$DATE/"

echo "Sandbox rebuilt at: $DEMO"
echo "  .prax/vault/ai-news-hub/$DATE/   ($(ls "$DEMO/.prax/vault/ai-news-hub/$DATE/" | wc -l | tr -d ' ') source .md files)"
echo
echo "Next:"
echo "  export OPENAI_API_KEY=..."
echo "  cd $DEMO"
echo "  prax prompt \"对 .prax/vault/ai-news-hub/$DATE/ 跑 knowledge-compile 技能\""
echo "  cd $SCRIPT_DIR && ./assertions.sh"
