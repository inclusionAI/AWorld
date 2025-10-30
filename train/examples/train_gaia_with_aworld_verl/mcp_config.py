import os

LOCAL_MCP_CONFIG = {
    "mcpServers": {
        "qwen_doc_server": {
            "command": "python",
            "args": [
                "-m",
                "train.examples.train_gaia_with_aworld_verl.qwen.qwen_file_parser"
            ],
        },
        "ms-playwright": {
            "command": "npx",
            "args": [
                "@playwright/mcp@0.0.37",
                "--no-sandbox",
                "--isolated",
                "--output-dir=/tmp/playwright",
                "--timeout-action=10000"
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


DISTRIBUTED_MCP_CONFIG = {
    "mcpServers": {
        "virtualpc-mcp-server": {
            "type": "streamable-http",
            "url": "http://mcp.aworldagents.com/vpc/mcp",
            "headers": {
                "Authorization": f"{os.getenv('MCP_AUTHORIZATION')}",
                # "MCP_SERVERS": "readweb-server,browseruse-server,documents-csv-server,documents-docx-server,documents-pptx-server,documents-pdf-server,documents-txt-server,download-server,intelligence-code-server,intelligence-think-server,intelligence-guard-server,media-audio-server,media-image-server,media-video-server,parxiv-server,terminal-server,wayback-server,wiki-server,googlesearch-server",

                "MCP_SERVERS": "ms-playwright,google-search,e2b-code-server,image-server,audio-server",
                # "MCP_SERVERS": "e2b-code-server",
                "IMAGE_ENV": f"{{\"E2B_API_KEY\":\"{os.getenv('MCP_E2B_API_KEY', '')}\"}}",
                # Specify environment variable values for tools on the client side, note JSON String structure
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


def build_mcp_config():
    if os.getenv('MCP_ENV', 'local') == 'local':
        # 确保必要的目录存在
        ensure_directories_exist()
        return LOCAL_MCP_CONFIG
    else:
        return DISTRIBUTED_MCP_CONFIG
