#!/bin/bash

handle_interrupt() {
    echo "Caught SIGINT (Ctrl+C). Executing cleanup commands..."
    echo "Shutting down localhost ports..."
    lsof -i :2000-2013 | awk 'NR>1 {print $2}' | sort -u | xargs -r kill -9
    echo "Cleanup done."
    exit 1
}
trap handle_interrupt SIGINT

python arxiv_server.py &
python audio_server.py &
python code_server.py &
python document_server.py &
python download_server.py &
python filesystem_server.py &
python googlemaps_server.py &
python image_server.py &
python math_server.py &
python reddit_server.py &
python search_server.py &
python sympy_server.py &
python video_server.py &
yes | npx @playwright/mcp@latest --port 2013 &