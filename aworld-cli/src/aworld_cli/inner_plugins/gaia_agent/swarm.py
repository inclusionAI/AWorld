

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, ModelConfig, AgentMemoryConfig, HistoryWriteStrategy, SummaryPromptConfig
from aworld.core.agent.swarm import TeamSwarm
from aworld.core.context.amni import ApplicationContext
from aworld.core.context.amni.config import ContextEnvConfig, AmniConfigFactory, AmniConfigLevel, AgentContextConfig, \
    get_default_config, CONTEXT_OFFLOAD_TOOL_NAME_WHITE, WorkingDirOssConfig
from aworldappinfra.core.registry import agent_team
from aworldappinfra.ui.ui_template import build_markdown_ui
from aworldappinfra.utils.warmup_utils import prepare_agent_sandbox_config
from aworldspace.agents.gaia_agent.financial_agent import FinancialDataAnalysisAgent
from aworldspace.agents.gaia_agent.flight_agnet import FlightSearchAgent
from aworldspace.agents.gaia_agent.gaia_agent import GaiaAgent
from aworldspace.agents.gaia_agent.gaia_mcp import gaia_mcp_config
from aworldspace.agents.gaia_agent.prompt.flight_prompt import search_sys_prompt, search_sys_prompt_en
from aworldspace.agents.gaia_agent.prompt.gaia_prompt import get_gaia_agent_system_prompt
from aworldspace.agents.gaia_agent.prompt.summary_prompt import *
from aworldspace.utils.model_config import get_model_config
from aworldspace.utils.mind_stream import set_generage_taget_agent_id


def build_context_config(debug_mode, env_config):
    config = get_default_config()
    config.debug_mode = debug_mode
    config.agent_config = AgentContextConfig(
        enable_system_prompt_augment=False,
        neuron_names= ["task", "working_dir", "todo", "action_info", "skills", "basic"],
        history_rounds= 30,
        history_scope='session',
        enable_summary=False,
        summary_rounds= 5,
        summary_context_length= 40960,
        summary_prompts= [
            SummaryPromptConfig(template=SUMMARY_TEMPLATE,
                                summary_rule=episode_memory_summary_rule,
                                summary_schema=episode_memory_summary_schema),
            SummaryPromptConfig(template=SUMMARY_TEMPLATE,
                                summary_rule=working_memory_summary_rule,
                                summary_schema=working_memory_summary_schema),
            SummaryPromptConfig(template=SUMMARY_TEMPLATE,
                                summary_rule=tool_memory_summary_rule,
                                summary_schema=tool_memory_summary_schema)
        ],
        tool_result_offload= False,
        tool_action_white_list= CONTEXT_OFFLOAD_TOOL_NAME_WHITE,
        tool_result_length_threshold= 30000
    )
    config.env_config = env_config
    return config


@agent_team(
    name="FlightSearchAgent-EN",
    desc="FlightSearchAgent-EN",
    context_config=AmniConfigFactory.create(
        AmniConfigLevel.PILOT,
        debug_mode=True,
        env_config=ContextEnvConfig(
            env_type="remote",
            env_config={
                "URL": "http://mcp.aworldagents.com/vpc-pre/mcp",
                "TOKEN": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhcHAiOiJhd29ybGRjb3JlLWFnZW50IiwidmVyc2lvbiI6MSwidGltZSI6MTc1NjM0ODcyMi45MTYyODd9.zM_l1VghOHaV6lC_0fYmZ35bLnH8uxIaA8iGeyuwQWY",
                "IMAGE_VERSION": "gaia-20251230182239",
                "warmup_config": {
                    "flight_search_agent_en": {
                        "flight-mcp": {
                            "image_version": "gaia-20251230182239",
                            "mcp_servers": "ms-playwright"
                        }
                    }
                }
            }
        )
    ),
    ui=build_markdown_ui,
    metadata={
        "version": "1.0.0",
        "creator": "aworld team",
        "create_time": "2025-11-03"
    }
)
def build_flight_agent(context: ApplicationContext) -> Agent:
    flight_agent_sandbox, flight_agent_mcp_servers, flight_agent_mcp_config = prepare_agent_sandbox_config(
        agent_name="flight_search_agent_en",
        context=context,
        mcp_servers=["flight-mcp"],
        fallback_mcp_config=gaia_mcp_config
    )
    return FlightSearchAgent(
        conf=AgentConfig(
            memory_config=AgentMemoryConfig(
                history_write_strategy=HistoryWriteStrategy.DIRECT
            ),
            llm_config=ModelConfig(
                llm_temperature=0.5,
                **get_model_config("airline_glm_sft_1113")
            ),
            use_vision=False
        ),
        agent_id=f"flight_search_agent_en_{context.session_id}",
        name="flight_search_agent_en",
        system_prompt=search_sys_prompt_en(),
        sandbox=flight_agent_sandbox,
        mcp_servers=flight_agent_mcp_servers,
        mcp_config=flight_agent_mcp_config  # 直接传递字典对象，不是模块引用
    )


@agent_team(
    name="FlightSearchAgent",
    desc="FlightSearchAgent",
    context_config=AmniConfigFactory.create(
        AmniConfigLevel.PILOT,
        debug_mode=True,
        env_config=ContextEnvConfig(
            env_type="remote",
            env_config={
                "URL": "http://mcp.aworldagents.com/vpc-pre/mcp",
                "TOKEN": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhcHAiOiJhd29ybGRjb3JlLWFnZW50IiwidmVyc2lvbiI6MSwidGltZSI6MTc1NjM0ODcyMi45MTYyODd9.zM_l1VghOHaV6lC_0fYmZ35bLnH8uxIaA8iGeyuwQWY",
                "IMAGE_VERSION": "gaia-20251230182239",
                "warmup_config": {
                    "flight_search_agent": {
                        "flight-mcp": {
                            "image_version": "gaia-20251230182239",
                            "mcp_servers": "ms-playwright"
                        }
                    }
                }
            }
        )
    ),
    ui=build_markdown_ui,
    metadata={
        "version": "1.0.0",
        "creator": "aworld team",
        "create_time": "2025-11-03"
    }
)
def build_flight_agent(context: ApplicationContext) -> Agent:
    flight_agent_sandbox, flight_agent_mcp_servers, flight_agent_mcp_config = prepare_agent_sandbox_config(
        agent_name="flight_search_agent",
        context=context,
        mcp_servers=["flight-mcp"],
        fallback_mcp_config=gaia_mcp_config
    )
    return FlightSearchAgent(
        conf=AgentConfig(
            memory_config=AgentMemoryConfig(
                history_write_strategy=HistoryWriteStrategy.DIRECT
            ),
            llm_config=ModelConfig(
                llm_temperature=0.5,
                **get_model_config("airline_glm_sft_1113")
            ),
            use_vision=False
        ),
        agent_id=f"flight_search_agent_{context.session_id}",
        name="flight_search_agent",
        system_prompt=search_sys_prompt(),
        sandbox=flight_agent_sandbox,
        mcp_servers=flight_agent_mcp_servers,
        mcp_config=flight_agent_mcp_config
    )




@agent_team(
        name="DeepResearch",
        desc="DeepResearch",
        context_config=build_context_config(
            debug_mode=True,
            env_config=ContextEnvConfig(
                env_type="remote",
                env_config={
                    "URL": "http://mcp.aworldagents.com/vpc-pre/mcp",
                    "TOKEN": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhcHAiOiJhd29ybGRjb3JlLWFnZW50IiwidmVyc2lvbiI6MSwidGltZSI6MTc1NjM0ODcyMi45MTYyODd9.zM_l1VghOHaV6lC_0fYmZ35bLnH8uxIaA8iGeyuwQWY",
                    "IMAGE_VERSION": "gaia-20251230182239",
                    "warmup_config": {
                        "gaia_agent": {
                            "gaia-mcp": {
                                "image_version": "gaia-20251230182239",
                                "mcp_servers": "googlesearch,readweb-server,media-audio,media-image,media-video,intell-code,intell-guard,doc-csv,doc-xlsx,doc-docx,doc-pptx,doc-txt,doc-pdf,download,parxiv-server,terminal-server,wayback-server,wiki-server"
                            }
                        },
                        "flight_agent": {
                            "flight-mcp": {
                                "image_version": "gaia-20251230182239",
                                "mcp_servers": "ms-playwright"
                            }
                        }
                    }
                },
                working_dir_base_path="aworld/workspaces",
                #working_dir_path_template="{base_path}/sid-trading_{session_id}_{task_id}",
                working_dir_oss_config=WorkingDirOssConfig.model_validate({
                    "access_key_id": os.environ.get("OSS_ACCESS_KEY_ID"),
                    "access_key_secret": os.environ.get("OSS_ACCESS_KEY_SECRET"),
                    "endpoint": os.environ.get("OSS_ENDPOINT"),
                    "bucket_name": os.environ.get("OSS_BUCKET_NAME")
                })
            )
        ),
        ui=build_markdown_ui,
        metadata={
            "version": "1.0.0",
            "creator": "aworld team",
            "create_time": "2025-11-03"
        }
)
async def build_gaia_swarm(context: ApplicationContext) -> TeamSwarm:
    # Prepare sandbox and MCP configuration
    gaia_agent_sandbox, gaia_agent_mcp_servers, gaia_agent_mcp_config = prepare_agent_sandbox_config(
        agent_name="gaia_agent",
        context=context,
        mcp_servers=["gaia-mcp"],
        fallback_mcp_config=gaia_mcp_config
    )

    # Create GaiaAgent with prepared configuration
    gaia_agent = GaiaAgent(
        conf=AgentConfig(
            llm_config=ModelConfig(
                llm_temperature=0.1,
                **get_model_config(os.environ["GAIA_AGENT_LLM_MODEL_NAME"])
            ),
            use_vision=False
        ),
        name="gaia_agent",
        agent_id=f"gaia_agent_{context.session_id}",
        system_prompt=get_gaia_agent_system_prompt(),
        sandbox=gaia_agent_sandbox,
        mcp_servers=gaia_agent_mcp_servers,
        mcp_config=gaia_agent_mcp_config
    )

    flight_agent_sandbox, flight_agent_mcp_servers, flight_agent_mcp_config = prepare_agent_sandbox_config(
        agent_name="flight_agent",
        context=context,
        mcp_servers=["flight-mcp"],
        fallback_mcp_config=gaia_mcp_config
    )
    flight_agent = FlightSearchAgent(
        conf=AgentConfig(
            memory_config=AgentMemoryConfig(
                history_write_strategy=HistoryWriteStrategy.DIRECT
            ),
            llm_config=ModelConfig(
                llm_temperature=0.5,
                **get_model_config("airline_glm_sft_1113")
            ),
            use_vision=False
        ),
        name="flight_search_agent",
        agent_id=f"flight_search_agent_{context.session_id}",
        system_prompt=search_sys_prompt(),
        sandbox=flight_agent_sandbox,
        mcp_servers=flight_agent_mcp_servers,
        mcp_config=flight_agent_mcp_config
    )

    financial_agent = FinancialDataAnalysisAgent(name="financial_agent",
                                                agent_id=f"financial_agent_{context.session_id}",
                                                desc = '')

    # TODO 插件化方式配置
    financial_agent_id = financial_agent.id()
    set_generage_taget_agent_id(context, financial_agent_id)
    return TeamSwarm(gaia_agent, flight_agent, financial_agent)
