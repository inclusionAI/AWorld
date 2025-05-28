FROM python:3.11-slim

RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/

RUN rm -f /etc/apt/sources.list.d/* && \
    echo "deb http://mirrors.aliyun.com/debian bookworm main contrib non-free non-free-firmware" > /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian bookworm-updates main contrib non-free non-free-firmware" >> /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian bookworm-backports main contrib non-free non-free-firmware" >> /etc/apt/sources.list && \
    echo "deb http://mirrors.aliyun.com/debian-security bookworm-security main contrib non-free non-free-firmware" >> /etc/apt/sources.list && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /app/

WORKDIR /app/

COPY . .

RUN python -m build && \
    pip install

CMD [ "mcp-openapi", "--config_path", "config.json", "--port", "9010" ]
