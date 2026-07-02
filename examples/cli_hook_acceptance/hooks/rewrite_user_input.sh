#!/bin/bash

if echo "$AWORLD_MESSAGE_JSON" | grep -Fq 'clean up build artifacts'; then
cat <<'EOF'
{
  "continue": true,
  "updated_input": {
    "content": "List the files under ./tmp/build first, then remove only build artifacts under ./tmp/build using the safest shell command you can."
  }
}
EOF
else
cat <<'EOF'
{
  "continue": true
}
EOF
fi
