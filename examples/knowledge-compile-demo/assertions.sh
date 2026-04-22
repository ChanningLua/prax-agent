#!/usr/bin/env bash
# Verify knowledge-compile's 6 hard contracts.

set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO="$SCRIPT_DIR/sandbox"
DATE=2026-04-21
VAULT="$DEMO/.prax/vault/ai-news-hub/$DATE"

cd "$DEMO" || { echo "FAIL: sandbox/ missing. Run replay.sh first."; exit 1; }
[ -d "$VAULT" ] || { echo "FAIL: vault dir missing"; exit 1; }

FAIL=0
check() {
    local desc="$1" cond="$2"
    if eval "$cond" >/dev/null 2>&1; then
        echo "  PASS  $desc"
    else
        echo "  FAIL  $desc"
        FAIL=$((FAIL + 1))
    fi
}

echo "Contract 1: index.md exists"
check "$VAULT/index.md exists and non-empty" "[ -s '$VAULT/index.md' ]"

echo
echo "Contract 2: daily-digest.md exists and is one-screen (≤ 60 lines)"
check "$VAULT/daily-digest.md exists and non-empty" "[ -s '$VAULT/daily-digest.md' ]"
if [ -s "$VAULT/daily-digest.md" ]; then
    LINES=$(wc -l < "$VAULT/daily-digest.md" | tr -d ' ')
    check "daily-digest.md ≤ 60 lines (got $LINES)" "[ $LINES -le 60 ]"
fi

echo
echo "Contract 3: topics/ subdirectory with ≥ 2 topic files"
check "$VAULT/topics/ is a dir" "[ -d '$VAULT/topics' ]"
if [ -d "$VAULT/topics" ]; then
    N=$(ls "$VAULT/topics"/*.md 2>/dev/null | wc -l | tr -d ' ')
    check "topics/*.md count ≥ 2 (got $N)" "[ $N -ge 2 ]"
    check "topics/*.md count ≤ 7 (got $N, upper-bound to prevent tag inflation)" "[ $N -le 7 ]"
fi

echo
echo "Contract 4: Obsidian double-links present (not markdown [] links)"
# Any topic file or index must reference source files with [[filename]]
GREP_DL=$(grep -rhE '\[\[[^]]+\]\]' "$VAULT/topics" "$VAULT/index.md" 2>/dev/null | head -1 || true)
check "at least one [[...]] link found" "[ -n '$GREP_DL' ]"

echo
echo "Contract 5: original source .md files NOT deleted"
for f in twitter-17242 twitter-17243 twitter-17244 zhihu-abc001 hn-40321 bilibili-bv001; do
    check "$f.md kept in $VAULT" "[ -f '$VAULT/$f.md' ]"
done

echo
echo "Contract 6: hard boundary — writes confined to vault dir"
STRAY=$(find "$DEMO" -type f -newer "$SCRIPT_DIR/replay.sh" \
    -not -path "$VAULT/*" \
    -not -path "$DEMO/.prax/sessions/*" \
    -not -path "$DEMO/.prax/todos.json" \
    2>/dev/null || true)
check "no stray writes outside $VAULT/" "[ -z '$STRAY' ]"

echo
if [[ $FAIL -eq 0 ]]; then
    echo "✅  All contracts PASSED"
    exit 0
else
    echo "❌  $FAIL contract(s) FAILED"
    exit 1
fi
