#!/bin/bash
cd "$(dirname "$0")"

pip install -r aworld/requirements.txt && \

pip install -r examples/gaia/requirements.txt && \

pip install -r examples/web/requirements.txt && \

streamlit run examples/web/main.py --server.port 8000