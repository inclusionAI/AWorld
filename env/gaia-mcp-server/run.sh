#!/bin/sh
cd "$(dirname "$0")"

sh ../mcp-server-base/build-image.sh && \

docker compose up --build --force-recreate -d && \

sleep 3 && \

open 'http://localhost:5901/vnc.html?autoconnect=true'