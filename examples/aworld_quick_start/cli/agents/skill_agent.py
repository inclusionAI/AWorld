"""
Skill-enabled Agent example for aworld-cli.

This demonstrates how to create an agent with multiple integrated skills and MCP tools,
enabling it to handle complex real-world tasks including document processing, web browsing,
task planning, and knowledge management.
"""
from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import TeamSwarm
from aworld_cli.core import agent

orchestrator_agent_system_prompt = """
You are a versatile AI assistant designed to solve any task presented by users.

## Task Description:
Note that tasks can be highly complex. Do not attempt to solve everything at once. You should break down the task and use different tools step by step. After using each tool, clearly explain the execution results and suggest the next steps.

Please use appropriate tools for the task, analyze the results obtained from these tools, and provide your reasoning. Always use available tools to verify correctness.

## Workflow:
1. **Task Analysis**: Analyze the task and determine the steps required to complete it. Propose a complete plan consisting of multi-step tuples (subtask, goal, action).
   - **Concept Understanding Phase**: Before task analysis, you must first clarify and translate ambiguous concepts in the task
   - **Terminology Mapping**: Convert broad terms into specific and accurate expressions
   - **Geographical Understanding**: Supplement and refine concepts based on geographical location
   - **Technical Term Precision**: Ensure accuracy and professionalism in terminology usage
2. **Information Gathering**: Prioritize using the model's prior knowledge to answer non-real-time world knowledge questions, avoiding unnecessary searches. For tasks requiring real-time information, specific data, or verification, collect necessary information from provided files or use search tools to gather comprehensive information.
3. **Tool Selection**: Select the most appropriate tool based on task characteristics.
   - **Code Mode Priority**: When a task requires multiple MCP tool calls (2+ times) or involves large intermediate results, prefer generating Python code to execute all operations at once instead of calling tools step by step. This reduces token usage by 95%+ and improves efficiency.
4. **Task Result Analysis**: Analyze the results obtained from the current task and determine whether the current task has been successfully completed.
5. **Final Answer**: If the task_input task has been solved. If the task is not yet solved, provide your reasoning, suggest, and report the next steps.
6. **Task Exit**: If the current task objective is completed, simply return the result without further reasoning to solve the overall global task goal, and without selecting other tools for further analysis.
7. **Ad-hoc Tasks**: If the task is a complete prompt, reason directly without calling any tools. If the prompt specifies an output format, respond according to the output format requirements.

# Output Requirements:
1. Before providing the `final answer`, carefully reflect on whether the task has been fully solved. If you have not solved the task, please provide your reasoning and suggest the next steps.
2. When providing the `final answer`, answer the user's question directly and precisely. For example, if asked "what animal is x?" and x is a monkey, simply answer "monkey" rather than "x is a monkey".

"""


@agent(
    name="SkillAgent",
    desc="A skill-enabled agent with integrated capabilities for document processing, web browsing, task planning, and knowledge management"
)
def build_skill_agent():
    """
    Build a skill-enabled agent with multiple integrated capabilities.
    
    This agent is equipped with various skills including:
    - Bash automation for command execution
    - Document processing (Excel, PDF, PPTX)
    - Task planning and progress tracking
    - Knowledge management and documentation
    - Web browser automation
    - Custom skills from the skills directory
    
    The agent also integrates with multiple MCP servers:
    - Playwright for browser automation
    - Filesystem server for file operations
    - Tavily for web search
    
    This configuration is suitable for complex real-world tasks that require
    multiple tools and capabilities working together.
    
    Returns:
        TeamSwarm: A TeamSwarm instance containing a single skill-enabled agent
    """
    import os
    from pathlib import Path

    from aworld.config import AgentConfig, ModelConfig
    from aworld.utils.skill_loader import collect_skill_docs


    PLANNING_SKILLS = {
        "planning": {
            "name": "Planning",
            "desc": "Task planning and progress tracking capability",
            "usage": "Create, manage and track todo.md to monitor task execution progress and organize work efficiently",
            "tool_list": {
                "filesystem-server": ["read_file", "write_file"]
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
                "ms-playwright": [],
                "tavily-mcp": []
            },
            "active": True
        }
    }

    SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"

    CUSTOM_SKILLS = collect_skill_docs(SKILLS_DIR)

    orchestrator_agent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_temperature=0.,
            llm_model_name=os.environ.get("LLM_MODEL_NAME"),
            llm_provider=os.environ.get("LLM_PROVIDER"),
            llm_api_key=os.environ.get("LLM_API_KEY"),
            llm_base_url=os.environ.get("LLM_BASE_URL")
        ),
        use_vision=False,
        skill_configs=PLANNING_SKILLS | BROWSER_SKILLS | CUSTOM_SKILLS
    )

    orchestrator_agent = Agent(
        name="SkillAgent",
        desc="A versatile agent with integrated skills for document processing, web browsing, planning, and knowledge management",
        conf=orchestrator_agent_config,
        system_prompt=orchestrator_agent_system_prompt,
        mcp_servers=["ms-playwright", "filesystem-server", "tavily-mcp"],
        mcp_config={
            "mcpServers": {
                "ms-playwright": {
                    "command": "npx",
                    "args": [
                        "@playwright/mcp@0.0.37",
                        "--no-sandbox",
                        "--output-dir=/tmp/playwright",
                        "--timeout-action=10000",
                    ],
                    "env": {
                        "PLAYWRIGHT_TIMEOUT": "120000",
                        "SESSION_REQUEST_CONNECT_TIMEOUT": "120"
                    }
                },
                "filesystem-server": {
                    "type": "stdio",
                    "command": "npx",
                    "args": [
                        "-y",
                        "@modelcontextprotocol/server-filesystem",
                        "~/workspace"
                    ]
                },
                "tavily-mcp": {
                    "type": "stdio",
                    "command": "npx",
                    "args": ["-y", "tavily-mcp@0.1.2"],
                    "env": {
                        "TAVILY_API_KEY": os.getenv('TAVILY_API_KEY'),
                        # Suppress startup messages
                        "NODE_ENV": "production"
                    }
                }
            }
        }
    )

    return TeamSwarm(orchestrator_agent)
