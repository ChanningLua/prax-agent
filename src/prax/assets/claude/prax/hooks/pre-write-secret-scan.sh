#!/usr/bin/env bash
# prax PreToolUse hook: secret detection
# Scans file content for potential secrets before writing.
# Exit 0 = allow, Exit 2 = block

set -euo pipefail

INPUT=$(cat)

# Extract content from Write/Edit tool input
CONTENT=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    content = data.get('tool_input', {}).get('content', '') or data.get('tool_input', {}).get('new_string', '')
    print(content)
except: pass
" 2>/dev/null || true)

if [ -z "$CONTENT" ]; then
  exit 0
fi

# Pattern-based secret detection
PATTERNS=(
  'AKIA[0-9A-Z]{16}'                    # AWS Access Key
  'sk-[a-zA-Z0-9]{20,}'                 # OpenAI/Stripe API Key
  'ghp_[a-zA-Z0-9]{36}'                 # GitHub Personal Token
  'glpat-[a-zA-Z0-9\-]{20,}'            # GitLab Token
  'xoxb-[0-9]{10,}-[a-zA-Z0-9]{20,}'    # Slack Bot Token
  'password\s*[:=]\s*["\x27][^"\x27]{8,}' # Hardcoded passwords
)

for PATTERN in "${PATTERNS[@]}"; do
  if echo "$CONTENT" | grep -qEi "$PATTERN"; then
    echo "{\"error\":\"Blocked: Potential secret detected matching pattern: ${PATTERN%%\\*}... Use environment variables instead.\"}"
    exit 2
  fi
done

exit 0
