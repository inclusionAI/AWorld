#!/bin/bash
cd "$(dirname "$0")"

pip install -e . && \

streamlit run examples/web/main.py --server.port 8000