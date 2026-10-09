ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive
ENV UV_NO_PROGRESS=1
ENV UV_DEFAULT_INDEX=https://pypi.org/simple

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-noto-cjk \
    libglib2.0-0 \
    libgl1 \
    libmagic1 \
    libreoffice-calc-nogui \
    libreoffice-impress-nogui \
    libreoffice-writer-nogui \
    poppler-utils \
    python3 \
    python3-pip \
    python3-venv \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/filex

RUN python3 -m pip install --break-system-packages --no-cache-dir uv

COPY aworld-tools/filex/pyproject.toml aworld-tools/filex/README.md aworld-tools/filex/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    UV_HTTP_TIMEOUT=300 UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple/ \
    uv sync --locked --no-dev --no-install-project

COPY aworld-tools/filex/bin ./bin
COPY aworld-tools/filex/config ./config
COPY aworld-tools/filex/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    UV_HTTP_TIMEOUT=300 UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple/ \
    uv sync --locked --no-dev \
    && chmod +x /opt/filex/bin/filex \
    && mkdir -p /root/.local/bin /root/workspace /skills \
    && ln -sf /opt/filex/bin/filex /root/.local/bin/filex

WORKDIR /opt/aworld

COPY pyproject.toml README.md LICENSE ./
COPY aworld ./aworld
COPY aworld-cli ./aworld-cli
COPY aworld-skills ./aworld-skills

RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv --python /usr/bin/python3 /opt/aworld/.venv \
    && uv pip install --python /opt/aworld/.venv/bin/python \
        -e '/opt/aworld[llm,skills]' \
        -e /opt/aworld/aworld-cli \
    && ln -sfn /opt/aworld/aworld-skills/filex /skills/filex

ENV PATH=/opt/aworld/.venv/bin:/root/.local/bin:/opt/filex/.venv/bin:${PATH}
ENV FILEX_WORKSPACE_ROOT=/root/workspace

WORKDIR /root/workspace

RUN filex --help >/dev/null \
    && aworld-cli --help >/dev/null \
    && aworld-cli run --demo --skill-path /skills/filex \
        --task "FileX skill smoke test" --json >/dev/null

CMD ["aworld-cli"]
