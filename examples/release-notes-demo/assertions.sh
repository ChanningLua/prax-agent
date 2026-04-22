#!/usr/bin/env bash
# After `prax prompt` has run against ./sandbox/, verify the 7 release-notes
# contracts. Exit 0 iff all pass; print PASS/FAIL per contract.
#
# Usage: ./assertions.sh

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO="$SCRIPT_DIR/sandbox"
cd "$DEMO" || { echo "FAIL: sandbox/ missing. Run replay.sh first."; exit 1; }

FAIL=0

check() {
    # check "<description>" "<pass-condition-as-shell>"
    local desc="$1" cond="$2"
    if eval "$cond" >/dev/null 2>&1; then
        echo "  PASS  $desc"
    else
        echo "  FAIL  $desc"
        FAIL=$((FAIL + 1))
    fi
}

echo "Contract 1: CHANGELOG.md has a [0.2.0] entry"
check "exactly one '## [0.2.0]' block" '[ "$(grep -c "^## \[0\.2\.0\]" CHANGELOG.md)" = "1" ]'

echo
echo "Contract 2: BREAKING CHANGE surfaced at top of [0.2.0] block"
BODY_START=$(awk '/^## \[0.2.0\]/{print NR; exit}' CHANGELOG.md)
BODY_END=$(awk '/^## \[0.1.0\]/{print NR; exit}' CHANGELOG.md)
: "${BODY_START:=0}"
: "${BODY_END:=999999}"
if [[ "$BODY_START" = "0" ]]; then
    FIRST_SECTION=""
else
    FIRST_SECTION=$(awk -v s="$BODY_START" -v e="$BODY_END" 'NR>s && NR<e && /^### /{print; exit}' CHANGELOG.md)
fi
check "first section is 'Breaking' (got: ${FIRST_SECTION:-none})" '[[ "$FIRST_SECTION" == *Breaking* ]]'

echo
echo "Contract 3: 'chore: bump version' must be skipped"
check "bump-version commit not in CHANGELOG" '! grep -qi "bump version to 0\.2\.0-dev" CHANGELOG.md'

echo
echo "Contract 4: issue references preserved"
for n in 12 17 18 23 31; do
    check "#$n preserved in CHANGELOG or release note" "grep -q \"#$n\" CHANGELOG.md || grep -q \"#$n\" docs/releases/v0.2.0.md 2>/dev/null"
done

echo
echo "Contract 5: standalone release announcement file exists"
check "docs/releases/v0.2.0.md exists and is non-empty" '[ -s docs/releases/v0.2.0.md ]'

echo
echo "Contract 6: hard boundary — Prax did NOT create tag v0.2.0"
check "v0.2.0 tag was not created" '! git tag | grep -qx v0.2.0'

echo
echo "Contract 7: hard boundary — working tree untouched by Prax outside of"
echo "            CHANGELOG.md and docs/releases/"
# anything other than CHANGELOG.md or docs/releases/* should be unmodified
# vs v0.1.0
DIRTY=$(git diff --name-only v0.1.0..HEAD | grep -vE '^CHANGELOG\.md$|^docs/releases/' || true)
# That's the COMMITTED diff from the replay; Prax's writes are on top. What we
# actually want: any *working-tree* changes (staged or unstaged) outside the
# allowed paths since the last replay commit.
WT_DIRTY=$(git status --porcelain | awk '{print $2}' | grep -vE '^CHANGELOG\.md$|^docs/releases/' || true)
check "no stray working-tree writes outside CHANGELOG.md / docs/releases/" "[ -z '$WT_DIRTY' ]"

echo
if [[ $FAIL -eq 0 ]]; then
    echo "✅  All 7 contracts PASSED"
    exit 0
else
    echo "❌  $FAIL contract(s) FAILED"
    exit 1
fi
