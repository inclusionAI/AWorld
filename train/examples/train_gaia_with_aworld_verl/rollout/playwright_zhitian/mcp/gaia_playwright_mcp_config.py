URL = "http://mcp.aworldagents.com/vpc-pre/mcp"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhcHAiOiJhd29ybGRjb3JlLWFnZW50IiwidmVyc2lvbiI6MSwidGltZSI6MTc1NjM0ODcyMi45MTYyODd9.zM_l1VghOHaV6lC_0fYmZ35bLnH8uxIaA8iGeyuwQWY"
# IMAGE_VERSION = "gaia-20251029120451"  # 环境包 by 九晨 caps = vision
IMAGE_VERSION = "gaia-20251023102638"  # 环境包 by 九晨 无vision
gaia_playwright_mcp_config = {
    "mcpServers": {
        "gaia-playwright-mcp": {
            "type": "streamable-http",
            "url": f"{URL}",
            "headers": {
                "Authorization": f"Bearer {TOKEN}",
                "MCP_SERVERS": "ms-playwright,googlesearch,readweb-server",
                "IMAGE_VERSION": f"{IMAGE_VERSION}"
            },
            "timeout": 6000,
            "sse_read_timeout": 6000,
            "client_session_timeout_seconds": 6000
        },
    }
}
