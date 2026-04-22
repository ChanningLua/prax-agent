#!/usr/bin/env bash
# Rebuild the demo repo from scratch under ./sandbox/, with a hand-picked
# 0.1.0 → HEAD commit history that exercises every branch of the
# release-notes skill's Conventional-Commits classifier:
#   feat / fix / refactor / perf / docs / chore / test / ci
#   + BREAKING CHANGE in commit body
#   + #NN issue references
#   + chore: bump version (MUST be skipped by the skill)
#
# Idempotent: wipes sandbox/ and recreates from zero each run.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO="$SCRIPT_DIR/sandbox"

# -- wipe and init ----------------------------------------------------------
rm -rf "$DEMO"
mkdir -p "$DEMO"
cd "$DEMO"

git init -q
git config user.email "demo@prax.local"
git config user.name  "Prax Demo"
git config commit.gpgsign false

# Use fixed commit dates so the CHANGELOG is reproducible day-to-day.
export GIT_AUTHOR_DATE="2026-01-01T10:00:00+08:00"
export GIT_COMMITTER_DATE="2026-01-01T10:00:00+08:00"

mk_commit() {
    # mk_commit "<subject>" "<body, possibly multi-line>"
    local subject="$1"
    local body="${2:-}"
    git add -A
    if [[ -n "$body" ]]; then
        git commit -q -m "$subject" -m "$body"
    else
        git commit -q -m "$subject"
    fi
    # Bump time 1 minute for each commit so `--no-merges` order is stable.
    GIT_AUTHOR_DATE="$(python3 -c "from datetime import datetime,timedelta;\
d=datetime.fromisoformat('${GIT_AUTHOR_DATE%+*}');\
d=d+timedelta(minutes=1);\
print(d.isoformat()+'+08:00')")"
    export GIT_AUTHOR_DATE GIT_COMMITTER_DATE="$GIT_AUTHOR_DATE"
}

# -- 0.1.0: initial scaffold ------------------------------------------------
mkdir -p src docs
cat > README.md <<'EOF'
# Demo Project
A minimal project used to verify the Prax release-notes skill end-to-end.
EOF
cat > CHANGELOG.md <<'EOF'
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-01-01

### Added
- Initial scaffold.
EOF
cat > src/auth.py <<'EOF'
def login(user):
    return True
EOF
cat > src/billing.py <<'EOF'
def invoice(user, amount):
    return {"user": user, "amount": amount}
EOF
mk_commit "chore: initial scaffold"
git tag v0.1.0

# -- commits destined for v0.2.0 --------------------------------------------

# 1. feat with issue ref
cat >> src/auth.py <<'EOF'

def oauth_login(provider, token):
    return True
EOF
mk_commit "feat(auth): add OAuth login support" "Implements the long-requested OAuth flow via Google, GitHub and Microsoft.

Refs #12"

# 2. feat with issue ref
cat >> src/billing.py <<'EOF'

def export_invoice_pdf(invoice_id):
    return f"/tmp/invoice-{invoice_id}.pdf"
EOF
mk_commit "feat(billing): invoice PDF export" "Customers can now download PDF invoices from the billing portal.

Refs #18"

# 3. fix with issue ref
sed -i '' 's|return True|# guard against double refresh\n    return True|' src/auth.py 2>/dev/null || \
    python3 -c "p='src/auth.py';s=open(p).read();open(p,'w').write(s.replace('return True','# guard against double refresh\n    return True',1))"
mk_commit "fix(auth): race on token refresh" "Mutex around the refresh path. Caught in staging.

Refs #17"

# 4. fix with issue ref
python3 -c "p='src/billing.py';s=open(p).read();open(p,'w').write(s.replace('amount}\n','amount, \"idempotency_key\": None}\n'))"
mk_commit "fix(billing): duplicate charge on retry" "Pass the idempotency key through so the upstream processor can de-dupe.

Refs #23"

# 5. refactor (no issue)
cat > src/core.py <<'EOF'
# split out from auth/billing
def now():
    import datetime; return datetime.datetime.utcnow()
EOF
mk_commit "refactor(core): extract shared time helper"

# 6. perf (no issue)
python3 -c "p='src/core.py';s=open(p).read();open(p,'w').write(s+'\n_CACHE = None\n\ndef config():\n    global _CACHE\n    if _CACHE is None: _CACHE = {}\n    return _CACHE\n')"
mk_commit "perf(core): cache config parse result"

# 7. docs
cat > docs/setup.md <<'EOF'
# Setup
1. Clone
2. Install
3. Run
EOF
mk_commit "docs: add setup guide"

# 8. docs (scoped)
cat > docs/auth.md <<'EOF'
# Authentication
OAuth via Google / GitHub / Microsoft.
EOF
mk_commit "docs(auth): explain MFA flow"

# 9. chore: bump version  — MUST be filtered out by release-notes skill
sed -i '' 's|^name = "demo"|name = "demo"\nversion = "0.2.0-dev"|' pyproject.toml 2>/dev/null || \
    echo 'version = "0.2.0-dev"' >> pyproject.toml
mk_commit "chore: bump version to 0.2.0-dev"

# 10. chore(deps) — borderline; skill should skip unless behavior changed
cat > requirements.txt <<'EOF'
httpx==0.28.0
pyyaml>=6.0
EOF
mk_commit "chore(deps): update httpx to 0.28"

# 11. test
mkdir -p tests
cat > tests/test_billing.py <<'EOF'
def test_invoice_has_idempotency_key():
    from src.billing import invoice
    assert "idempotency_key" in invoice("u", 10)
EOF
mk_commit "test: add integration tests for billing"

# 12. ci
mkdir -p .github/workflows
cat > .github/workflows/ci.yml <<'EOF'
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pytest
EOF
mk_commit "ci: switch to GitHub Actions"

# 13. BREAKING CHANGE — must be placed at top of the CHANGELOG entry
python3 -c "p='src/core.py';s=open(p).read();open(p,'w').write(s+'\n\ndef api_response(data):\n    # envelope change\n    return {\"data\": data}\n')"
mk_commit "feat(api): wrap responses in data envelope" "POST /v1/users now returns {\"data\": {...}} instead of the raw object.
Consumer code MUST unwrap \`.data\` before use.

BREAKING CHANGE: response envelope changed
Refs #31"

# -- done -------------------------------------------------------------------
echo
echo "Sandbox rebuilt at: $DEMO"
echo "Tag v0.1.0 at: $(git rev-parse --short v0.1.0)"
echo "HEAD:          $(git rev-parse --short HEAD)"
echo
git log v0.1.0..HEAD --oneline --no-merges
