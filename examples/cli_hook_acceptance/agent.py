import subprocess
from pathlib import Path

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig
from aworld.core.agent.swarm import Swarm
from aworld.core.common import ActionModel, Observation
from aworld.core.tool.func_to_tool import be_tool
from aworld_cli.core import agent


def _resolve_workdir(cwd: str) -> Path:
    path = Path(cwd)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


@be_tool(
    tool_name="bash",
    tool_desc="Run a shell command inside the current workspace for the CLI hook acceptance demo.",
)
def run_command(command: str, cwd: str = ".") -> str:
    workdir = _resolve_workdir(cwd)
    result = subprocess.run(
        command,
        shell=True,
        cwd=workdir,
        text=True,
        capture_output=True,
    )

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()

    if result.returncode != 0:
        details = stderr or stdout or "no output"
        return f"command failed ({result.returncode}): {details}"

    return stdout or "(no output)"


class CliHookDemoAgent(Agent):
    async def async_policy(
        self,
        observation: Observation,
        info=None,
        message=None,
        **kwargs,
    ):
        content = (observation.content or "").strip()
        lower_content = content.lower()

        if observation.is_tool_result:
            audit_logged = str((observation.info or {}).get("audit_logged", False)).lower()
            audit_source = (observation.info or {}).get("audit_source", "none")
            return [
                ActionModel(
                    agent_name=self.id(),
                    policy_info=(
                        "Safe cleanup finished. "
                        f"audit_logged={audit_logged}; "
                        f"audit_source={audit_source}; "
                        f"tool_output={content or '(no output)'}"
                    ),
                )
            ]

        if "remove ./tmp/build" in lower_content and "rm -rf" in lower_content:
            return [
                ActionModel(
                    tool_name="bash",
                    action_name="run_command",
                    params={"command": "rm -rf ./tmp/build"},
                    agent_name=self.id(),
                    policy_info="Attempting destructive cleanup for the hook demo.",
                )
            ]

        if "remove only ./tmp/build/demo.txt safely" in lower_content:
            return [
                ActionModel(
                    tool_name="bash",
                    action_name="run_command",
                    params={"command": "ls -la ./tmp/build && rm -f ./tmp/build/demo.txt"},
                    agent_name=self.id(),
                    policy_info="Listing ./tmp/build and removing only demo.txt safely.",
                )
            ]

        if "remove only build artifacts under ./tmp/build" in lower_content:
            return [
                ActionModel(
                    agent_name=self.id(),
                    policy_info=(
                        "Scoped cleanup request accepted: inspect ./tmp/build first and only "
                        "touch artifacts under ./tmp/build."
                    ),
                )
            ]

        return [
            ActionModel(
                agent_name=self.id(),
                policy_info="This demo agent only handles the four README acceptance prompts for ./tmp/build.",
            )
        ]


@agent(
    name="CliHookAcceptanceAgent",
    desc="Deterministic demo agent for manual aworld-cli hook acceptance walkthroughs",
)
def build_cli_hook_acceptance_swarm():
    demo_agent = CliHookDemoAgent(
        name="cli_hook_demo_agent",
        desc=(
            "A deterministic demo agent for cleanup-oriented tasks in ./tmp/build. "
            "It emits predictable shell-style tool calls so hook behavior is easy to validate."
        ),
        conf=AgentConfig(),
        tool_names=["bash"],
    )

    return Swarm(demo_agent)
