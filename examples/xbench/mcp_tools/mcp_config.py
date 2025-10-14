import os

MCP_CONFIG = {
    "mcpServers": {
        "ms-playwright": {
            "command": "npx",
            "args": [
                "@playwright/mcp@0.0.37",
                "--no-sandbox",
                # "--isolated",
                "--output-dir=/tmp/playwright",
                "--timeout-action=10000"
                "--cdp-endpoint=http://localhost:9222"
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
                "mcp_servers.image_server"
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
                "mcp_servers.document_server"
            ],
            "env": {
                "SESSION_REQUEST_CONNECT_TIMEOUT": "120"
            }
        },
        "terminal-server": {
            "command": "python",
            "args": [
                "-m",
                "mcp_servers.terminal_server"
            ],
            "env": {
            }
        },
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
                "mcp_tools.contextserver"
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
                'RERANKER_BASE_URL': 'https://antchat.alipay.com/v1',
                'RERANKER_API_KEY': 'zLGL7xFPTTLbWYraGdb4jsdxTiQ0IPrQ',
                'RERANKER_MODEL_NAME': 'Qwen3_Reranker_8B',
                'LLM_BASE_URL': os.environ['LLM_BASE_URL'],
                'LLM_MODEL_NAME': os.environ['LLM_MODEL_NAME'],
                'LLM_API_KEY': os.environ['LLM_API_KEY']
            }
        }
    }
}
