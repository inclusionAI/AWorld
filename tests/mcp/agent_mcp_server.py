from aworld.experimental.mcp_server.server import AgentMCP
from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig
from aworld.logs.util import logger

mcp = AgentMCP()

agent_config = AgentConfig(
    llm_provider="openai",
    llm_model_name="claude-3-7-sonnet-20250219",
    llm_base_url="",
    llm_api_key="",
)

agent = Agent(
    conf=agent_config,
    name="faq_agent",
    desc="I am a helpful assistant that can answer questions by using the search_api tool.",
    system_prompt="You are a helpful assistant that can answer questions by using the search_api tool.",
    tool_names=["search_api"]
)
logger.info("agent created")

mcp.add_agent(agent)
logger.info("agent added to mcp server")

if __name__ == "__main__":
    try:
        logger.info("start to run mcp server")
        mcp.run(transport="stdio")
    except Exception as e:
        import traceback
        logger.error(traceback.format_exc())
