#!/bin/bash

handle_interrupt() {
    echo "Caught SIGINT (Ctrl+C). Executing cleanup commands..."
    echo "Shutting down localhost ports..."
    lsof -i :2000-2013 | awk 'NR>1 {print $2}' | sort -u | xargs -r kill -9
    echo "Cleanup done."
    exit 1
}

trap handle_interrupt SIGINT

python arxiv_server.py --port 2000 &
ARXIV_PID=$!
python audio_server.py --port 2001 &
AUDIO_PID=$!
python code_server.py --port 2002 &
CODE_PID=$!
python document_server.py --port 2003 &
DOCUMENT_PID=$!
python download_server.py --port 2004 &
DOWNLOAD_PID=$!
python filesystem_server.py --port 2005 &
FILESYSTEM_PID=$!
python github_server.py --port 2006 &
GITHUB_PID=$!
python googlemaps_server.py --port 2007 &
GOOGLEMAPS_PID=$!
python image_server.py --port 2008 &
IMAGE_PID=$!
python math_server.py --port 2009 &
MATH_PID=$!
python reddit_server.py --port 2010 &
REDDIT_PID=$!
python search_server.py --port 2011 &
SEARCH_PID=$!
python sympy_server.py --port 2012 &
SYMPY_PID=$!
python video_server.py --port 2013 &
VIDEO_PID=$!
yes | npx @playwright/mcp@latest --port 2014 &
PLAYWRIGHT_PID=$!

wait $ARXIV_PID $AUDIO_PID $CODE_PID $DOCUMENT_PID $DOWNLOAD_PID $FILESYSTEM_PID $GITHUB_PID $GOOGLEMAPS_PID $IMAGE_PID $MATH_PID $REDDIT_PID $SEARCH_PID $SYMPY_PID $VIDEO_PID $PLAYWRIGHT_PID

echo "All servers have been started."