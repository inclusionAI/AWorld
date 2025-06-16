#!/bin/bash
cd "$(dirname "$0")"

pip install -e . && \

pip install -r mcp_servers/requirements.txt && \

<<<<<<< Updated upstream
cd examples/web && aworld web
=======
cd examples/command && aworld web
>>>>>>> Stashed changes
