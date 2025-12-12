import os
from pathlib import Path

from aworld.config import AgentConfig, ModelConfig
from aworld.utils.skill_loader import collect_skill_docs


BASIC_SKILLS = {
    "bash": {
        "name": "Bash",
        "desc": "Bash automation and manipulation capability",
        "usage": "Automate bash tasks, manipulate files, and execute bash commands",
        "tool_list": {
            "terminal-server": ["execute_command"]
        },
        "active": True
    }
}


DOCUMENT_SKILLS = {
    "excel": {
        "name": "Excel",
        "desc": "Excel automation and manipulation capability",
        "usage": "Automate Excel tasks, manipulate spreadsheets, and extract information from Excel files",
        "tool_list": {
            "document_server": ["mcpreadexcel"]
        }
    },
    "pdf": {
        "name": "PDF",
        "desc": "PDF automation and manipulation capability",
        "usage": "Automate PDF tasks, manipulate PDF files, and extract information from PDF files, "
                 "if is remote pdf url ,please use browser skill first download it",
        "tool_list": {
            "document_server": ["mcpreadpdf"]
        }
    },
    "pptx": {
        "name": "PPTX",
        "desc": "PPTX automation and manipulation capability",
        "usage": "Automate PPTX tasks, manipulate PowerPoint presentations, and extract information from PPTX files",
        "tool_list": {
            "document_server": ["mcpreadpptx"]
        }
    },
}

PLANNING_SKILLS = {
    "planning": {
        "name": "Planning",
        "desc": "Task planning and progress tracking capability",
        "usage": "Create, manage and track todos to monitor task execution progress and organize work efficiently",
        "tool_list": {
            "amnicontext-server": ["add_todo", "get_todo"]
        },
        "active": True
    },
    "scratchpad": {
        "name": "Scratchpad",
        "desc": "Knowledge management and documentation capability",
        "usage": "Create, update, and manage knowledge documents to record key information, findings, and insights during task execution",
        "tool_list": {
            "amnicontext-server": ["add_knowledge", "get_knowledge", "grep_knowledge", "list_knowledge_info",
                                   "update_knowledge"]
        },
        "active": True
    }
}

BROWSER_SKILLS = {
    "browser": {
        "name": "Browser",
        "desc": "Web browser automation and interaction capability",
        "usage": "Automate web browsing tasks, navigate websites, interact with web elements, and extract information from web pages",
        "tool_list": {
            "ms-playwright": []
        },
        "active": True
    }
}

SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"

# CUSTOM_SKILLS = collect_skill_docs(SKILLS_DIR)
CUSTOM_SKILLS = collect_skill_docs("/Users/wuhulala/Documents/agiwork/skills")

orchestrator_agent_config = AgentConfig(
    llm_config=ModelConfig(
        llm_temperature=0.,
        llm_model_name=os.environ.get("LLM_MODEL_NAME"),
        llm_provider=os.environ.get("LLM_PROVIDER"),
        llm_api_key=os.environ.get("LLM_API_KEY"),
        llm_base_url=os.environ.get("LLM_BASE_URL")
    ),
    use_vision=False,
    skill_configs= PLANNING_SKILLS | BROWSER_SKILLS| CUSTOM_SKILLS
    # skill_configs= PLANNING_SKILLS | BASIC_SKILLS | BROWSER_SKILLS
)
