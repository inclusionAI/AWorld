#!/bin/bash

cd "$(dirname "$0")"

docker build -t mcp-server-base -f Dockerfile .
