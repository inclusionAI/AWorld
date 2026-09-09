import os

debug_mode = os.getenv("DEBUG_MODE", "false").lower() == "true"
container_server_port = int(os.getenv("CONTAINER_SERVER_PORT", "9000"))

docker_registry_url = os.getenv("DOCKER_REGISTRY_URL")
docker_registry_user_name = os.getenv("DOCKER_REGISTRY_USER_NAME")
docker_registry_password = os.getenv("DOCKER_REGISTRY_PASSWORD")

gateway_server_addr = os.getenv("GATEWAY_SERVER_ADDR", "http://mcp-gateway:8000")

default_mcp_server_image_version = os.getenv(
    "DEFAULT_MCP_SERVER_IMAGE_VERSION", "latest"
)

mcp_server_image_name = os.getenv("MCP_SERVER_IMAGE_NAME")

docker_mode = os.getenv("DOCKER_MODE", "dind")

mcp_container_mem_limit = os.getenv("MCP_CONTAINER_MEM_LIMIT", "8G")

docker_health_timeout_sec = float(os.getenv("DOCKER_HEALTH_TIMEOUT_SEC", "3"))
