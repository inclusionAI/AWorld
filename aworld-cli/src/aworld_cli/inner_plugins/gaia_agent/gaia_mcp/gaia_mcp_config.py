URL = "http://mcp.aworldagents.com/vpc-pre/mcp"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhcHAiOiJhd29ybGRjb3JlLWFnZW50IiwidmVyc2lvbiI6MSwidGltZSI6MTc1NjM0ODcyMi45MTYyODd9.zM_l1VghOHaV6lC_0fYmZ35bLnH8uxIaA8iGeyuwQWY"
IMAGE_VERSION = "gaia-20251230182239"
gaia_mcp_config = {
    "mcpServers": {
        "gaia-mcp": {
            "type": "streamable-http",
            "url": f"{URL}",
            "headers": {
                "env_name": "gaia",
                "Authorization": f"Bearer {TOKEN}",
                "MCP_SERVERS": "googlesearch,readweb-server,media-audio,media-image,media-video,intell-code,"
                               "intell-guard,doc-csv,doc-xlsx,doc-docx,doc-pptx,doc-txt,doc-pdf,download,parxiv-server"
                               "terminal-server,wayback-server,wiki-server",
                "IMAGE_VERSION": f"{IMAGE_VERSION}"
            },
            "timeout": 6000,
            "sse_read_timeout": 6000,
            "client_session_timeout_seconds": 6000


        },
        "flight-mcp": {
            "type": "streamable-http",
            "url": f"{URL}",
            "headers": {
                "env_name": "flight",
                "Authorization": f"Bearer {TOKEN}",
                "MCP_SERVERS": "ms-playwright",
                "IMAGE_VERSION": f"{IMAGE_VERSION}"
            },
            "timeout": 6000,
            "sse_read_timeout": 6000,
            "client_session_timeout_seconds": 6000
        },
    }
}
