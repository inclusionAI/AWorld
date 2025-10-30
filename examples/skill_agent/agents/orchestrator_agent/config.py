import os

from aworld.config import AgentConfig, ModelConfig

orchestrator_agent_config = AgentConfig(
    llm_config=ModelConfig(
        llm_temperature=0.1,
        llm_model_name=os.environ.get("LLM_MODEL_NAME"),
        llm_provider=os.environ.get("LLM_PROVIDER"),
        llm_api_key=os.environ.get("LLM_API_KEY"),
        llm_base_url=os.environ.get("LLM_BASE_URL")
    ),
    use_vision=False,
    skill_configs={
        "browser": {
            "name": "浏览器",
            "desc": "浏览器",
            "usage": "使用浏览器",
            "tool_list": {
                "ms-playwright": []
            }
        },
        "planning": {
            "name": "规划能力",
            "desc": "使用todo监控任务执行进度",
            "usage": "使用todo监控任务执行进度",
            "active": True,
            "tool_list": {
                "amnicontext-server": ["add_todo", "get_todo"]
            }
        },
        "scratchpad": {
            "name": "文档编辑",
            "desc": "文档编辑",
            "usage": "使用文档编辑能力，记录任务执行过程中的关键信息",
            "tool_list": {
                "amnicontext-server": ["add_knowledge", "get_knowledge", "grep_knowledge", "list_knowledge_info",
                                       "update_knowledge"]
            }
        }
    }
)

orchestrator_mcp_servers = ["amnicontext-server", "ms-playwright"]
