#!/bin/bash
cd "$(dirname "$0")"

pip install -e . && \

pip install -r mcp_servers/requirements.txt && \

cd examples/web && aworld run