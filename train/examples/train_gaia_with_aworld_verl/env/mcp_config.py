import json
import os

from aworld.logs.util import logger
from train.examples.train_gaia_with_aworld_verl.env.ip_pool import get_proxy_server


async def build_local_mcp_config():
    return {
        "mcpServers": {
            "qwen_file_parser": {
                "command": "python",
                "args": [
                    "-m",
                    "train.examples.train_gaia_with_aworld_verl.qwen.qwen_file_parser"
                ],
            },
            "ms-playwright": {
                "command": "npx",
                "args": [
                    "@playwright/mcp@latest",
                    "--no-sandbox",
                    "--isolated",
                    "--output-dir=/tmp/playwright",
                    "--timeout-action=10000"
                    # "--browser", "chromium"
                ],
                "env": {
                    "PLAYWRIGHT_TIMEOUT": "120000",
                    "SESSION_REQUEST_CONNECT_TIMEOUT": "120"
                }
            },
            "image_server": {
                "command": "python",
                "args": [
                    "-m",
                    "examples.xbench.mcp_tools.image_server"
                ],
                "env": {
                    "LLM_API_KEY": os.environ.get("IMAGE_LLM_API_KEY"),
                    "LLM_MODEL_NAME": os.environ.get("IMAGE_LLM_MODEL_NAME"),
                    "LLM_BASE_URL": os.environ.get("IMAGE_LLM_BASE_URL"),
                    "SESSION_REQUEST_CONNECT_TIMEOUT": "60"
                }
            },
            "document_server": {
                "command": "python",
                "args": [
                    "-m",
                    "examples.xbench.mcp_tools.document_server"
                ],
                "env": {
                    "SESSION_REQUEST_CONNECT_TIMEOUT": "120"
                }
            },
            "terminal-controller": {
                "command": "python",
                "args": ["-m", "terminal_controller"]
            },
            # "terminal-server": {
            #     "command": "python",
            #     "args": [
            #         "-m",
            #         "examples.xbench.mcp_tools.terminal_server"
            #     ],
            #     "env": {
            #     }
            # },
            "filesystem-server": {
                "type": "stdio",
                "command": "npx",
                "args": [
                    "-y",
                    "@modelcontextprotocol/server-filesystem",
                    "/tmp/workspace"
                ]
            },
            "amnicontext-server": {
                "command": "python",
                "args": [
                    "-m",
                    "examples.xbench.mcp_tools.contextserver"
                ],
                "env": {
                    "AMNI_RAG_TYPE": os.environ['AMNI_RAG_TYPE'],
                    "WORKSPACE_TYPE": os.environ['WORKSPACE_TYPE'],
                    "WORKSPACE_PATH": os.environ['WORKSPACE_PATH'],
                    "CHUNK_PROVIDER": os.environ['CHUNK_PROVIDER'],
                    "CHUNK_SIZE": os.environ['CHUNK_SIZE'],
                    "CHUNK_OVERLAP": os.environ['CHUNK_OVERLAP'],
                    "CHUNK_SEPARATOR": os.environ['CHUNK_SEPARATOR'],
                    "EMBEDDING_PROVIDER": os.environ['EMBEDDING_PROVIDER'],
                    "EMBEDDING_BASE_URL": os.environ['EMBEDDING_BASE_URL'],
                    "EMBEDDING_API_KEY": os.environ['EMBEDDING_API_KEY'],
                    "EMBEDDING_MODEL_NAME": os.environ['EMBEDDING_MODEL_NAME'],
                    "EMBEDDING_MODEL_DIMENSIONS": os.environ['EMBEDDING_MODEL_DIMENSIONS'],
                    "DB_PATH": os.environ['DB_PATH'],
                    "VECTOR_STORE_PROVIDER": os.environ['VECTOR_STORE_PROVIDER'],
                    "CHROMA_PATH": os.environ['CHROMA_PATH'],
                    "ELASTICSEARCH_URL": os.environ['ELASTICSEARCH_URL'],
                    "ELASTICSEARCH_INDEX_PREFIX": os.environ['ELASTICSEARCH_INDEX_PREFIX'],
                    "ELASTICSEARCH_USERNAME": os.environ['ELASTICSEARCH_USERNAME'],
                    "ELASTICSEARCH_PASSWORD": os.environ['ELASTICSEARCH_PASSWORD'],
                    'RERANKER_PROVIDER': 'http',
                    'RERANKER_BASE_URL': os.environ['RERANKER_BASE_URL'],
                    'RERANKER_API_KEY': os.environ['RERANKER_API_KEY'],
                    'RERANKER_MODEL_NAME': os.environ['RERANKER_MODEL_NAME'],
                    'LLM_BASE_URL': os.environ['LLM_BASE_URL'],
                    'LLM_MODEL_NAME': os.environ['LLM_MODEL_NAME'],
                    'LLM_API_KEY': os.environ['LLM_API_KEY']
                }
            }
        }
    }


async def build_distributed_mcp_config():
    return {
        "mcpServers": {
            "virtualpc-mcp-server": {
                "type": "streamable-http",
                "url": "http://mcp.aworldagents.com/vpc/mcp",
                "headers": {
                    "Authorization": f"{os.getenv('MCP_AUTHORIZATION')}",
                    # "MCP_SERVERS": "readweb-server,browseruse-server,documents-csv-server,documents-docx-server,documents-pptx-server,documents-pdf-server,documents-txt-server,download-server,intelligence-code-server,intelligence-think-server,intelligence-guard-server,media-audio-server,media-image-server,media-video-server,parxiv-server,terminal-server,wayback-server,wiki-server,googlesearch-server",

                    # "MCP_SERVERS": "ms-playwright,google-search,e2b-code-server,image-server,audio-server",
                    "MCP_SERVERS": "ms-playwright",
                    # "MCP_SERVERS": "e2b-code-server",
                    # "IMAGE_ENV": json.dumps({"E2B_API_KEY": os.getenv('MCP_E2B_API_KEY', '')}),
                    "IMAGE_ENV": json.dumps({"E2B_API_KEY": os.getenv('MCP_E2B_API_KEY', '')}) if os.getenv("IP_POOL_ENABLE", "False") == "False"
                            else json.dumps({"E2B_API_KEY": os.getenv('MCP_E2B_API_KEY', ''), "PLAYWRIGHT_PROXY_SERVER": await get_proxy_server()}),
                    # Specify environment variable values for tools on the client side, note JSON String structure
                    "IMAGE_VERSION": f"{os.getenv('IMAGE_VERSION', '')}" if os.getenv("IP_POOL_ENABLE", "False") == "False"
                            else f"{os.getenv('IP_POOL_IMAGE_VERSION', '')}",
                },
                "timeout": 600,
                "sse_read_timeout": 600,
                "client_session_timeout_seconds": 600
            },
            "amnicontext-server": {
                "command": "python",
                "args": [
                    "-m",
                    "examples.xbench.mcp_tools.contextserver"
                ],
                "env": {
                    "AMNI_RAG_TYPE": os.environ['AMNI_RAG_TYPE'],
                    "WORKSPACE_TYPE": os.environ['WORKSPACE_TYPE'],
                    "WORKSPACE_PATH": os.environ['WORKSPACE_PATH'],
                    "CHUNK_PROVIDER": os.environ['CHUNK_PROVIDER'],
                    "CHUNK_SIZE": os.environ['CHUNK_SIZE'],
                    "CHUNK_OVERLAP": os.environ['CHUNK_OVERLAP'],
                    "CHUNK_SEPARATOR": os.environ['CHUNK_SEPARATOR'],
                    "EMBEDDING_PROVIDER": os.environ['EMBEDDING_PROVIDER'],
                    "EMBEDDING_BASE_URL": os.environ['EMBEDDING_BASE_URL'],
                    "EMBEDDING_API_KEY": os.environ['EMBEDDING_API_KEY'],
                    "EMBEDDING_MODEL_NAME": os.environ['EMBEDDING_MODEL_NAME'],
                    "EMBEDDING_MODEL_DIMENSIONS": os.environ['EMBEDDING_MODEL_DIMENSIONS'],
                    "DB_PATH": os.environ['DB_PATH'],
                    "VECTOR_STORE_PROVIDER": os.environ['VECTOR_STORE_PROVIDER'],
                    "CHROMA_PATH": os.environ['CHROMA_PATH'],
                    "ELASTICSEARCH_URL": os.environ['ELASTICSEARCH_URL'],
                    "ELASTICSEARCH_INDEX_PREFIX": os.environ['ELASTICSEARCH_INDEX_PREFIX'],
                    "ELASTICSEARCH_USERNAME": os.environ['ELASTICSEARCH_USERNAME'],
                    "ELASTICSEARCH_PASSWORD": os.environ['ELASTICSEARCH_PASSWORD'],
                    'RERANKER_PROVIDER': 'http',
                    'RERANKER_BASE_URL': os.environ['RERANKER_BASE_URL'],
                    'RERANKER_API_KEY': os.environ['RERANKER_API_KEY'],
                    'RERANKER_MODEL_NAME': os.environ['RERANKER_MODEL_NAME'],
                    'LLM_BASE_URL': os.environ['LLM_BASE_URL'],
                    'LLM_MODEL_NAME': os.environ['LLM_MODEL_NAME'],
                    'LLM_API_KEY': os.environ['LLM_API_KEY']
                }
            }
        }
    }


def ensure_directories_exist():
    """确保所有必要的目录存在"""
    # 基本工作目录
    os.makedirs('/tmp/workspace', exist_ok=True)
    os.makedirs('/tmp/playwright', exist_ok=True)

    # 从环境变量中获取的路径
    workspace_path = os.environ.get('WORKSPACE_PATH')
    if workspace_path:
        os.makedirs(workspace_path, exist_ok=True)

    db_path = os.environ.get('DB_PATH')
    if db_path:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

    chroma_path = os.environ.get('CHROMA_PATH')
    if chroma_path:
        os.makedirs(chroma_path, exist_ok=True)


async def build_mcp_config(user_input: str = None, session_id: str = None, task_id: str = None):
    if os.getenv('MCP_ENV', 'local') == 'local':
        # 确保必要的目录存在
        ensure_directories_exist()
        mcp_config = await build_local_mcp_config()
    else:
        mcp_config = await build_distributed_mcp_config()

    logger.info(f"user_input={user_input}|session_id={session_id}|task_id={task_id}|mcp_config={mcp_config}")
    # 未开启，移除相关的配置
    if os.getenv('GAIA_AGENT_CONTEXT', 'common') == 'common' and 'amnicontext-server' in mcp_config['mcpServers']:
        del mcp_config['mcpServers']['amnicontext-server']

    return mcp_config
