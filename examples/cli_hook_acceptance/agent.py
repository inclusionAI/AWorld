import os

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, ModelConfig
from aworld.core.agent.swarm import Swarm
from aworld_cli.core import agent


DEMO_SYSTEM_PROMPT = """
You are a narrow demo agent for the CLI hook acceptance walkthrough.

When the task involves files or cleanup inside the current workspace:
- Prefer a shell or terminal-style tool so hook behavior is visible.
- Inspect the target path before deleting anything.
- Use the safest, narrowest command you can.
- Never use rm -rf unless the user explicitly asks for destructive cleanup.
"""


@agent(
    name="CliHookAcceptanceAgent",
    desc="Demo AWorld agent for manual aworld-cli hook acceptance walkthroughs",
)
def build_cli_hook_acceptance_swarm():
    agent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name=os.environ.get("LLM_MODEL_NAME", "gpt-4"),
            llm_provider=os.environ.get("LLM_PROVIDER", "openai"),
            llm_api_key=os.environ.get("LLM_API_KEY"),
            llm_base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
            llm_temperature=float(os.environ.get("LLM_TEMPERATURE", "0.2")),
        )
    )

    demo_agent = Agent(
        name="cli_hook_demo_agent",
        desc=(
            "A narrow demo agent for cleanup-oriented tasks. Prefer explicit, safe shell "
            "commands scoped to the current example workspace. Never use rm -rf unless the "
            "user explicitly requests destructive cleanup."
        ),
        conf=agent_config,
        system_prompt=DEMO_SYSTEM_PROMPT,
    )

    return Swarm(demo_agent)
