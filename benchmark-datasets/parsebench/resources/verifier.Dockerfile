FROM {base_image}
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONNOUSERSITE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY scorer/ /tmp/parsebench-build/
RUN python /tmp/parsebench-build/install_scorer.py \
    && python -m venv /opt/parsebench-scorer/.venv \
    && /opt/parsebench-scorer/.venv/bin/python -m pip install --no-cache-dir --require-hashes --only-binary=:all: --index-url https://pypi.org/simple -r /tmp/parsebench-build/build-requirements.txt \
    && /opt/parsebench-scorer/.venv/bin/python -m pip install --no-cache-dir --require-hashes --no-build-isolation --only-binary=:all: --no-binary=fuzzysearch --index-url https://pypi.org/simple -r /tmp/parsebench-build/requirements.txt \
    && /opt/parsebench-scorer/.venv/bin/python -m pip check \
    && rm -rf /tmp/parsebench-build
ENV PATH="/opt/parsebench-scorer/.venv/bin:${PATH}" \
    PYTHONPATH=/opt/parsebench-scorer/src
RUN python -c "import parse_bench.cli; from parse_bench.evaluation.cli import EvaluationCLI; print('Official ParseBench CLI and evaluator imports passed')"
WORKDIR /tests
