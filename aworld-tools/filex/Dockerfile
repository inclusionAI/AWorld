ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive
ENV UV_NO_PROGRESS=1

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

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY bin ./bin
COPY config ./config
COPY src ./src
RUN uv sync --frozen --no-dev \
    && chmod +x /opt/filex/bin/filex \
    && mkdir -p /root/.local/bin /root/workspace \
    && ln -sf /opt/filex/bin/filex /root/.local/bin/filex

ENV PATH=/root/.local/bin:/opt/filex/.venv/bin:${PATH}
ENV FILEX_WORKSPACE_ROOT=/root/workspace

WORKDIR /root/workspace
