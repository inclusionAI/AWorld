import os

cluster_name = os.getenv("CLUSTER_NAME", "mcp_gateway")

debug_mode = os.getenv("DEBUG_MODE", "false").lower() == "true"

vnc_auth = os.getenv("VNC_AUTH", "true").lower() == "true"

channel_auth = os.getenv("CHANNEL_AUTH", "true").lower() == "true"

redis_url = os.getenv("MCP_GATEWAY_REDIS_URL", "redis://localhost:6379/0")

token_secret = os.getenv("MCP_GATEWAY_TOKEN_SECRET")

"""
Session cleanup interval in seconds.
"""
SESSION_CLEAN_INTERVAL_SEC = 10 * 60

"""
Env instance max idle time after last active time.
"""
MAX_LAST_ACTIVE_TIME_SEC = 3 * 60 * 60

"""
Env instance max idle time after created time.
"""
MAX_CREATED_AT_TIME_SEC = 24 * 60 * 60
