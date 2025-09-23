#!/bin/sh
cd "$(dirname "$0")"

sh gaia-mcp-server/run.sh && \

open 'http://localhost:5901/vnc.html?autoconnect=true'