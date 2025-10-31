import os

from aworld.config import AgentConfig, ModelConfig

BASIC_SKILLS = {
    "bash": {
        "name": "Bash",
        "desc": "Bash automation and manipulation capability",
        "usage": "Automate bash tasks, manipulate files, and execute bash commands",
        "tool_list": {
            "terminal-server": ["execute_command"]
        }
    }
}

WEBSITE_SKILLS = {

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

BROWSER_SKILLS = {
    "browser": {
        "name": "Browser",
        "desc": "Web browser automation and interaction capability",
        "usage": "Automate web browsing tasks, navigate websites, interact with web elements, and extract information from web pages",
        "tool_list": {
            "ms-playwright": []
        }
    }
}

TEXT_SKILLS = {
    "arxiv_research": {
        "name": "ArXiv Research Guide",
        "desc": "Best practices for searching and analyzing academic papers",
        "usage": """
1. Use specific search terms from the paper's abstract or title
2. Navigate to https://arxiv.org/search/?query=<keywords>&searchtype=all
3. Filter by category (cs.AI, cs.CL, etc.) and date range
4. Access PDF directly via https://arxiv.org/pdf/<paper_id>.pdf
5. Check citations and related work for additional papers
        """
    },

    "github_navigation": {
        "name": "GitHub Navigation Guide",
        "desc": "Efficient strategies for analyzing repositories",
        "usage": """
1. Start with README.md for project overview
2. Check /docs or /documentation for detailed guides
3. Review /examples for usage patterns
4. Examine /tests for implementation details
5. Check Issues and Discussions for common problems
        """
    }
}

orchestrator_agent_config = AgentConfig(
    llm_config=ModelConfig(
        llm_temperature=0.1,
        llm_model_name=os.environ.get("LLM_MODEL_NAME"),
        llm_provider=os.environ.get("LLM_PROVIDER"),
        llm_api_key=os.environ.get("LLM_API_KEY"),
        llm_base_url=os.environ.get("LLM_BASE_URL")
    ),
    use_vision=False,
    skill_configs=BASIC_SKILLS | DOCUMENT_SKILLS | PLANNING_SKILLS | BROWSER_SKILLS | TEXT_SKILLS
)
