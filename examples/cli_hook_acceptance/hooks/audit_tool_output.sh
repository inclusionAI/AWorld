#!/bin/bash

cat <<'EOF'
{
  "continue": true,
  "system_message": "Audit hook observed a safe tool call.",
  "updated_output": {
    "info": {
      "audit_logged": true,
      "audit_source": "cli_hook_acceptance"
    }
  }
}
EOF
