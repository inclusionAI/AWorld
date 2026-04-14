#!/bin/bash

if echo "$AWORLD_MESSAGE_JSON" | grep -Fq 'rm -rf'; then
cat <<'EOF'
{
  "continue": true,
  "permission_decision": "deny",
  "permission_decision_reason": "Destructive command blocked by before_tool_call hook"
}
EOF
else
cat <<'EOF'
{
  "continue": true
}
EOF
fi
