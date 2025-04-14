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
ARXIV_PID=$!
python audio_server.py &
AUDIO_PID=$!
python code_server.py &
CODE_PID=$!
python document_server.py &
DOCUMENT_PID=$!
python download_server.py &
DOWNLOAD_PID=$!
python filesystem_server.py &
FILESYSTEM_PID=$!
python googlemaps_server.py &
GOOGLEMAPS_PID=$!
python image_server.py &
IMAGE_PID=$!
python math_server.py &
MATH_PID=$!
python reddit_server.py &
REDDIT_PID=$!
python search_server.py &
SEARCH_PID=$!
python sympy_server.py &
SYMPY_PID=$!
python video_server.py &
VIDEO_PID=$!
yes | npx @playwright/mcp@latest --port 2013 &
PLAYWRIGHT_PID=$!

wait $ARXIV_PID $AUDIO_PID $CODE_PID $DOCUMENT_PID $DOWNLOAD_PID $FILESYSTEM_PID $GOOGLEMAPS_PID $IMAGE_PID $MATH_PID $REDDIT_PID $SEARCH_PID $SYMPY_PID $VIDEO_PID $PLAYWRIGHT_PID

echo "All servers have been started."