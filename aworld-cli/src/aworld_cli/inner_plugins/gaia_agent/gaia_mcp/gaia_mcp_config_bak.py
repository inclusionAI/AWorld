gaia_mcp_config = {
    "mcpServers": {
        "media-audio-server": {
            "command": "python",
            "args": [
                "-m",
                "mcp_servers.media_audio_server"
            ],
            "env": {
                "AUDIO_LLM_BASE_URL": "${AUDIO_LLM_BASE_URL}",
                "AUDIO_LLM_MODEL_NAME": "${AUDIO_LLM_MODEL_NAME}",
                "AUDIO_LLM_API_KEY": "${AUDIO_LLM_API_KEY}"
            }
        },
        "media-image-server": {
            "command": "python",
            "args": [
                "-m",
                "mcp_servers.media_image_server"
            ],
            "env": {
                "IMAGE_LLM_BASE_URL": "${IMAGE_LLM_BASE_URL}",
                "IMAGE_LLM_MODEL_NAME": "${IMAGE_LLM_MODEL_NAME}",
                "IMAGE_LLM_API_KEY": "${IMAGE_LLM_API_KEY}"
            }
        },
        "media-video-server": {
            "command": "python",
            "args": [
                "-m",
                "mcp_servers.media_video_server"
            ],
            "env": {
                "VIDEO_LLM_BASE_URL": "${VIDEO_LLM_BASE_URL}",
                "VIDEO_LLM_MODEL_NAME": "${VIDEO_LLM_MODEL_NAME}",
                "VIDEO_LLM_API_KEY": "${VIDEO_LLM_API_KEY}"
            }
        },
        "intelligence-code-server": {
            "command": "python",
            "args": [
                "-m",
                "mcp_servers.intelligence_code_server"
            ],
            "env": {
                "CODE_LLM_BASE_URL": "${CODE_LLM_BASE_URL}",
                "CODE_LLM_MODEL_NAME": "${CODE_LLM_MODEL_NAME}",
                "CODE_LLM_API_KEY": "${CODE_LLM_API_KEY}"
            }
        },
        "intelligence-guard-server": {
            "command": "python",
            "args": [
                "-m",
                "mcp_servers.intelligence_guard_server"
            ],
            "env": {
                "GUARD_LLM_BASE_URL": "${GUARD_LLM_BASE_URL}",
                "GUARD_LLM_MODEL_NAME": "${GUARD_LLM_MODEL_NAME}",
                "GUARD_LLM_API_KEY": "${GUARD_LLM_API_KEY}"
            }
        },
        "intelligence-think-server": {
            "command": "python",
            "args": [
                "-m",
                "mcp_servers.intelligence_think_server"
            ],
            "env": {
                "THINK_LLM_BASE_URL": "${THINK_LLM_BASE_URL}",
                "THINK_LLM_MODEL_NAME": "${THINK_LLM_MODEL_NAME}",
                "THINK_LLM_API_KEY": "${THINK_LLM_API_KEY}"
            }
        },
        "documents-csv-server": {
            "command": "python",
            "args": [
                "-m",
                "mcp_servers.documents_csv_server"
            ],
            "env": {
            }
        },
        "documents-docx-server": {
            "command": "python",
            "args": [
                "-m",
                "mcp_servers.documents_docx_server"
            ],
            "env": {
            }
        },
        "documents-pptx-server": {
            "command": "python",
            "args": [
                "-m",
                "mcp_servers.documents_pptx_server"
            ],
            "env": {
            }
        },
        "documents-pdf-server": {
            "command": "python",
            "args": [
                "-m",
                "mcp_servers.documents_pdf_server"
            ],
            "env": {
            }
        },
        "documents-txt-server": {
            "command": "python",
            "args": [
                "-m",
                "mcp_servers.documents_txt_server"
            ],
            "env": {
            }
        },
        "download-server": {
            "command": "python",
            "args": [
                "-m",
                "mcp_servers.download_server"
            ],
            "env": {
            }
        },
        "google-search-server": {
            "command": "python",
            "args": [
                "-m",
                "mcp_servers.google_search_server"
            ],
            "env": {
                "GOOGLE_API_KEY": "${GOOGLE_API_KEY}",
                "GOOGLE_CSE_ID": "${GOOGLE_CSE_ID}"
            }
        },
        "parxiv-server": {
            "command": "python",
            "args": [
                "-m",
                "mcp_servers.parxiv_server"
            ],
            "env": {
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
        "wayback-server": {
            "command": "python",
            "args": [
                "-m",
                "mcp_servers.wayback_server"
            ],
            "env": {
            }
        },
        "wiki-server": {
            "command": "python",
            "args": [
                "-m",
                "mcp_servers.wiki_server"
            ],
            "env": {
            }
        }
    }
}
