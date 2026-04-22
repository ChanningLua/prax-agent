#!/usr/bin/env bash
# After `prax prompt` has run support-digest against ./sandbox/, verify the
# 6 hard contracts. Exit 0 iff all pass.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO="$SCRIPT_DIR/sandbox"
DATE=2026-04-21
cd "$DEMO" || { echo "FAIL: sandbox/ missing. Run replay.sh first."; exit 1; }

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

DIGEST=".prax/vault/support/${DATE}/digest.md"
REDACTED=".prax/vault/support/${DATE}/tickets-redacted.json"
ARCHIVE=".prax/inbox/archive/tickets-${DATE}.json"

echo "Contract 1: digest.md exists and is non-empty"
check "file under .prax/vault/support/${DATE}/" "[ -s '$DIGEST' ]"

echo
echo "Contract 2: digest contains a 'top N' highlights section capped at 5"
# Count highlight items in two supported layouts:
#   a) ### Headings     (H3 per highlight)
#   b) numbered list     (  1. foo / 2. bar …)
# BSD awk on macOS does not support /pat/I; tolower() is portable.
HIGHLIGHT_COUNT=$(awk 'tolower($0)~/highlight|亮点|top/{f=1;next} f' "$DIGEST" 2>/dev/null \
    | grep -cE '^### |^[[:space:]]*[0-9]+\.' 2>/dev/null)
HIGHLIGHT_COUNT=$(printf '%s' "${HIGHLIGHT_COUNT:-0}" | tr -d '\n ' | awk '{print $1+0}')
check "digest mentions '亮点' or 'highlights' or 'Top'" "grep -qiE '亮点|highlights|top' '$DIGEST'"
check "highlights section has 1-5 items (got $HIGHLIGHT_COUNT)" "[ $HIGHLIGHT_COUNT -ge 1 ] && [ $HIGHLIGHT_COUNT -le 5 ]"

echo
echo "Contract 3: PII redacted — raw email addresses must NOT appear in digest"
# The sample data has these literal emails; they should be masked in digest.
for email in "jane.doe@example.com" "sam@example.net" "user+oauth@test.co" "unhappy@example.com"; do
    check "$email is NOT in digest" "! grep -q '$email' '$DIGEST'"
done

echo
echo "Contract 4: redacted-data file exists and ALSO has PII masked"
check "$REDACTED exists and non-empty" "[ -s '$REDACTED' ]"
if [ -s "$REDACTED" ]; then
    for email in "jane.doe@example.com" "sam@example.net"; do
        check "$email NOT in $REDACTED" "! grep -q '$email' '$REDACTED'"
    done
fi

echo
echo "Contract 5: original ticket file was archived, not left in inbox"
check "archive/tickets-${DATE}.json exists" "[ -s '$ARCHIVE' ]"
check "inbox/tickets-${DATE}.json was moved (no longer at original location)" "[ ! -f '.prax/inbox/tickets-${DATE}.json' ]"

echo
echo "Contract 6: hard boundary — working tree writes confined to .prax/"
# The skill should only touch .prax/ and its subdirs.
STRAY=$(find . -type f -newer "$SCRIPT_DIR/replay.sh" \
    -not -path './.prax/*' \
    -not -path './.git/*' \
    2>/dev/null || true)
check "no stray writes outside .prax/" "[ -z '$STRAY' ]"

echo
if [[ $FAIL -eq 0 ]]; then
    echo "✅  All contracts PASSED"
    exit 0
else
    echo "❌  $FAIL contract(s) FAILED"
    exit 1
fi
