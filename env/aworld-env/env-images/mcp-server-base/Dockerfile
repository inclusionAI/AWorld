FROM python:3.13-slim-bookworm as base

# Avoid prompts from apt
ENV DEBIAN_FRONTEND=noninteractive

# Configure mirror
ARG ENABLE_MIRROR=false
ENV PIP_INDEX_URL=${ENABLE_MIRROR:+https://mirrors.aliyun.com/pypi/simple}
RUN if [ "$ENABLE_MIRROR" = "true" ]; then \
    rm -rfv /etc/apt/sources.list.d/* && \
    echo "deb http://ftp.cn.debian.org/debian bookworm main contrib non-free non-free-firmware" > /etc/apt/sources.list && \
    echo "deb http://ftp.cn.debian.org/debian bookworm-updates main contrib non-free non-free-firmware" >> /etc/apt/sources.list && \
    echo "deb http://ftp.cn.debian.org/debian bookworm-backports main contrib non-free non-free-firmware" >> /etc/apt/sources.list && \
    echo "deb http://ftp.cn.debian.org/debian-security bookworm-security main contrib non-free non-free-firmware" >> /etc/apt/sources.list && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*; \
    fi

# Install dependencies
RUN apt-get update && apt-get install -y \
    xvfb \
    x11vnc \
    openbox \
    supervisor \
    novnc \
    websockify \
    procps \
    xdg-utils \
    python3-xdg \
    x11-xserver-utils \
    tint2 \
    gdebi-core \
    git \
    vim \
    less \
    lsof \
    tree \
    curl \
    wget \
    unzip \
    --no-install-recommends && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - && \
    apt-get install -y nodejs

# Install noVNC
RUN git clone --depth 1 --branch v1.6.0.fix https://github.com/eric68668/noVNC.git /usr/local/novnc \
    && git clone --depth 1 --branch v0.13.0 https://github.com/novnc/websockify /usr/local/novnc/utils/websockify

# Set up working directory
WORKDIR /app/view_server

# Set up supervisord configuration
COPY docker/resources/supervisord.conf /etc/supervisor/supervisord.conf

# Copy scripts
COPY docker/resources/x11-setup.sh x11-setup.sh

# Make scripts executable
RUN chmod +x x11-setup.sh
COPY docker/resources/openbox-rc.xml /root/.config/openbox/rc.xml
COPY docker/resources/tint2rc /root/.config/tint2/tint2rc

ENV NO_VNC_PORT=5901

# Set the display environment variable
ENV DISPLAY=:0

# Screen resolution and DPI configuration
ENV SCREEN_WIDTH=1600
ENV SCREEN_HEIGHT=1200
ENV SCREEN_DPI=96

COPY docker/resources/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# MCP Server Container
FROM base

RUN pip install -U --break-system-packages uv

COPY mcp_server_proxy /app/mcp_server_proxy/

RUN cd /app/mcp_server_proxy && uv sync

# Compile Python bytecode to improve import performance
# -q: quiet mode, -f: force recompilation, -j 0: use all CPU cores
RUN cd /app/mcp_server_proxy && \
    PYTHON=$(if [ -d .venv ]; then echo ".venv/bin/python"; else echo "python3"; fi) && \
    $PYTHON -m compileall -q -f -j 0 src/ && \
    ($PYTHON -m compileall -q -f -j 0 .venv/lib/python*/site-packages/ 2>/dev/null || true)

WORKDIR /root/workspace

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=10s --start-period=60s --retries=6 \
    CMD curl -f http://localhost:8080/health || exit 1

ENV TZ=Asia/Shanghai

CMD ["supervisord", "-n", "-c", "/etc/supervisor/supervisord.conf", "--pidfile", "/var/run/supervisord.pid"]