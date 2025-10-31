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
                "--timeout-action=10000",
                "--cdp-endpoint=http://localhost:9222"
            ],
            "env": {
                "PLAYWRIGHT_TIMEOUT": "120000",
                "SESSION_REQUEST_CONNECT_TIMEOUT": "120"
            }
        },
        "document_server": {
            "command": "python",
            "args": [
                "-m",
                "mcp_tools.document_server"
            ],
            "env": {
                "SESSION_REQUEST_CONNECT_TIMEOUT": "120"
            }
        },
        "terminal-server": {
            "command": "python",
            "args": [
                "-m",
                "mcp_tools.terminal_server"
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
                "AMNI_RAG_TYPE": os.environ.get('AMNI_RAG_TYPE', 'amni'),
                "WORKSPACE_TYPE": os.environ.get('WORKSPACE_TYPE'),
                "WORKSPACE_PATH": os.environ.get('WORKSPACE_PATH'),
                "CHUNK_PROVIDER": os.environ.get('CHUNK_PROVIDER'),
                "CHUNK_SIZE": os.environ.get('CHUNK_SIZE'),
                "CHUNK_OVERLAP": os.environ.get('CHUNK_OVERLAP'),
                "CHUNK_SEPARATOR": os.environ.get('CHUNK_SEPARATOR'),
                "EMBEDDING_PROVIDER": os.environ.get('EMBEDDING_PROVIDER'),
                "EMBEDDING_BASE_URL": os.environ.get('EMBEDDING_BASE_URL'),
                "EMBEDDING_API_KEY": os.environ.get('EMBEDDING_API_KEY'),
                "EMBEDDING_MODEL_NAME": os.environ.get('EMBEDDING_MODEL_NAME'),
                "EMBEDDING_MODEL_DIMENSIONS": os.environ.get('EMBEDDING_MODEL_DIMENSIONS'),
                "DB_PATH": os.environ.get('DB_PATH'),
                "VECTOR_STORE_PROVIDER": os.environ.get('VECTOR_STORE_PROVIDER'),
                "CHROMA_PATH": os.environ.get('CHROMA_PATH'),
                "ELASTICSEARCH_URL": os.environ.get('ELASTICSEARCH_URL'),
                "ELASTICSEARCH_INDEX_PREFIX": os.environ.get('ELASTICSEARCH_INDEX_PREFIX'),
                "ELASTICSEARCH_USERNAME": os.environ.get('ELASTICSEARCH_USERNAME'),
                "ELASTICSEARCH_PASSWORD": os.environ.get('ELASTICSEARCH_PASSWORD'),
                'RERANKER_PROVIDER': 'http',
                'RERANKER_BASE_URL': os.environ.get('RERANKER_BASE_URL'),
                'RERANKER_API_KEY': os.environ.get('RERANKER_API_KEY'),
                'RERANKER_MODEL_NAME': os.environ.get('RERANKER_MODEL_NAME'),
                'LLM_BASE_URL': os.environ.get('LLM_BASE_URL'),
                'LLM_MODEL_NAME': os.environ.get('LLM_MODEL_NAME'),
                'LLM_API_KEY': os.environ.get('LLM_API_KEY')
            }
        }
    }
}
