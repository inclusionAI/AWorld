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
            "name": "Browser",
            "desc": "Web browser automation and interaction capability",
            "usage": "Automate web browsing tasks, navigate websites, interact with web elements, and extract information from web pages",
            "tool_list": {
                "ms-playwright": []
            }
        },
        "planning": {
            "name": "Planning",
            "desc": "Task planning and progress tracking capability",
            "usage": "Create, manage and track todos to monitor task execution progress and organize work efficiently",
            "active": True,
            "tool_list": {
                "amnicontext-server": ["add_todo", "get_todo"]
            }
        },
        "scratchpad": {
            "name": "Scratchpad",
            "desc": "Knowledge management and documentation capability",
            "usage": "Create, update, and manage knowledge documents to record key information, findings, and insights during task execution",
            "tool_list": {
                "amnicontext-server": ["add_knowledge", "get_knowledge", "grep_knowledge", "list_knowledge_info",
                                       "update_knowledge"]
            }
        }
    }
)
