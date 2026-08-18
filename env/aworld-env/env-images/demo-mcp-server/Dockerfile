FROM mcp-server-base:latest

RUN apt-get update && apt-get install -y \
    libterm-readline-perl-perl \
    libmupdf-dev \
    libmagic1

COPY docker/gaia_dataset /root/workspace/gaia_dataset

COPY mcp_servers/init_env.sh /app/mcp_servers/

COPY mcp_servers/*/pyproject.toml /app/mcp_servers/

RUN sh /app/mcp_servers/init_env.sh

COPY mcp_servers /app/mcp_servers

CMD ["sh", "-c", "sh /app/view_server/start.sh && sh /app/mcp_servers/start_hook.sh && cd /app/mcp_server_proxy && uv run --no-sync -m mcp_server_proxy.main"]