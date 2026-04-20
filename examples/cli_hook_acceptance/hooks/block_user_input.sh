#!/bin/bash

if echo "$AWORLD_MESSAGE_JSON" | grep -Eiq 'please run rm -rf /tmp/build immediately|delete everything|wipe the workspace'; then
cat <<'EOF'
{
  "continue": true,
  "permission_decision": "deny",
  "permission_decision_reason": "Destructive prompt blocked before agent execution"
}
EOF
else
cat <<'EOF'
{
  "continue": true
}
EOF
fi
