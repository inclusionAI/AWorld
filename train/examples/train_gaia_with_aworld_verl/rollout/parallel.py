import logging
import os
import traceback
from datetime import datetime
from typing import Optional

from aworld.config.conf import AgentConfig, ModelConfig
from train.examples.train_gaia_with_aworld_verl.rollout.playwright_zhitian.executor_agent_shell import GaiaPlayWrightAgent
from train.examples.train_gaia_with_aworld_verl.rollout.playwright_zhitian.flight_plan_agent import FlightPlanAgent
from train.examples.train_gaia_with_aworld_verl.rollout.playwright_zhitian.mcp.gaia_playwright_mcp_config import gaia_playwright_mcp_config
from train.examples.train_gaia_with_aworld_verl.rollout.playwright_zhitian.mcp.gaia_playwright_mcp_servers import gaia_playwright_mcp_servers
from train.examples.train_gaia_with_aworld_verl.rollout.playwright_zhitian.prompt.flight_plan_prompt import get_flight_plan_agent_system_prompt
from train.examples.train_gaia_with_aworld_verl.rollout.playwright_zhitian.prompt.gaia_playwright_prompt import get_gaia_playwright_agent_system_prompt
from aworld.core.agent.swarm import Swarm, TeamSwarm
from aworld.core.task import TaskResponse, Task
from aworld.evaluations.base import EvalTarget, EvalDataCase
from aworld.runner import Runners
from aworld.runners.state_manager import RuntimeStateManager
from train.examples.train_gaia_with_aworld_verl.env import build_mcp_config
from train.examples.train_gaia_with_aworld_verl.log_processor.pyspy_context import pyspy_profile
from train.examples.train_gaia_with_aworld_verl.rollout import build_gaia_agent, build_gaia_task
from aworld.core.context.amni.config import get_default_config, init_middlewares, AgentContextConfig

logging.basicConfig(level=logging.INFO, force=True, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

log_path = os.path.join("logs", "eval_digest.log")

# Use RotatingFileHandler for size-based rotation (100MB per file, keep 10 files)
from logging.handlers import RotatingFileHandler

file_handler = RotatingFileHandler(
    log_path,
    maxBytes=30 * 1024 * 1024,  # 100MB per file
    backupCount=10,  # Keep 10 backup files
    encoding='utf-8'
)
eval_digest_logger = logging.getLogger("eval_digest")
eval_digest_logger.setLevel(level=logging.INFO)

eval_digest_logger.addHandler(file_handler)


class ParallelFlightEvalTarget(EvalTarget[dict]):

    def __init__(
            self
    ):
        super().__init__()

    async def build_common_gaia_task(self, user_input: str, session_id, task_id):
        if 'screen_shot' in os.getenv("ENV_PLUGINS", ""):
            from ..env.hooks import PostLLMCallRolloutHook, PostToolCallRolloutHook

        # agent = build_gaia_agent(llm_model_name=os.getenv("LLM_MODEL_NAME"),
        #                                llm_base_url=os.getenv("LLM_BASE_URL"),
        #                                llm_api_key=os.getenv("LLM_API_KEY"),
        #                                mcp_config=await build_mcp_config())
        a = await self._build_swarm()
        return await build_gaia_task(user_input=user_input, target=a, timeout=1200)

    async def _build_swarm(self) -> Optional[Swarm]:
        init_middlewares()
        agent_config_plan = AgentConfig(
            llm_config=ModelConfig(
                llm_model_name=os.getenv("FLIGHT_PLAN_AGENT_LLM_MODEL_NAME"),
                llm_base_url=os.getenv("FLIGHT_PLAN_AGENT_LLM_MODEL_URL"),
                llm_api_key=os.getenv("FLIGHT_PLAN_AGENT_LLM_MODEL_API_KEY"),
            ),
            # memory_config=AgentMemoryConfig(history_rounds=4),
            use_vision=False
        )

        agent_config_execute = AgentConfig(
            llm_config=ModelConfig(
                llm_model_name=os.getenv("FLIGHT_EXECUTOR_AGENT_LLM_MODEL_NAME"),
                llm_base_url=os.getenv("FLIGHT_EXECUTOR_AGENT_LLM_MODEL_URL"),
                llm_api_key=os.getenv("FLIGHT_EXECUTOR_AGENT_LLM_MODEL_API_KEY"),
            ),
            # memory_config=AgentMemoryConfig(history_rounds=4),
            use_vision=False
        )

        plan_agent = FlightPlanAgent(
            conf=agent_config_plan,
            name="plan_agent",
            system_prompt=get_flight_plan_agent_system_prompt(),
            mcp_servers=gaia_playwright_mcp_servers,
            mcp_config=gaia_playwright_mcp_config

        )

        a = get_flight_plan_agent_system_prompt()
        print('planner_system_prompt ', a)

        execute_agent = GaiaPlayWrightAgent(
            conf=agent_config_execute,
            name="exec_agent",
            agent_id = "flight_search_agent",
            system_prompt=get_gaia_playwright_agent_system_prompt(),
            mcp_servers=gaia_playwright_mcp_servers,
            mcp_config=gaia_playwright_mcp_config
        )
        return TeamSwarm(plan_agent, execute_agent, max_steps=100)

    async def predict(self, index: int, o_input: EvalDataCase[dict]) -> dict:
        batch_id = o_input.run_id
        case_data = o_input.case_data or {}

        # Some CSV exports may include a UTF-8 BOM in the header, leading to '\ufeffid'
        case_id = (
            case_data.get('id')
            or case_data.get('\ufeffid')
            or case_data.get('case_id')
            or case_data.get('sample_id')
            or f"case_{index}"
        )

        prompt = case_data.get('prompt') or case_data.get('\ufeffprompt')
        if prompt is None:
            raise KeyError("prompt")

        session_id = f"{batch_id}_session#{case_id}"
        task_id = f"{batch_id}_task#{case_id}"
        task = await self.build_common_gaia_task(user_input=prompt, session_id=session_id, task_id=task_id)
        task_id = task.id

        try:
            # ============= RUN TASK WITH PY-SPY PROFILING =============
            # 使用 py-spy 进行性能分析，每个任务独立统计
            # 可以通过环境变量 ENABLE_PYSPY=true 来启用，或者直接设置 enable=True
            output_path = f"logs/flame_graphs/{batch_id}/flame_{task_id}"
            
            with pyspy_profile(
                output=output_path,
                rate=100,  # 采样频率
                subprocesses=True,  # 包含子进程
                native=False,  # 不包含原生代码（C扩展）
                formats=['svg'],  # 输出格式：svg, raw, flamegraph, speedscope
                enable=None  # None 表示从环境变量 ENABLE_PYSPY 读取
            ):
                result = await Runners.run_task(task=task)
            os.makedirs(f"logs/trajectory/{batch_id}", exist_ok=True)
            with open(f"logs/trajectory/{batch_id}/traj_{index}.json", "a") as f:
                f.write(str(result[task_id].trajectory))
            os.makedirs(f"logs/results/{batch_id}", exist_ok=True)
            cur_time = datetime.now().strftime('%Y%m%d%H%M%S')
            with open(f"logs/results/{batch_id}/{task_id}_{cur_time}_{o_input.eval_case_id}.txt", "w") as f:
                f.write(result[task_id].answer)
            
            # 任务结束后，查询state_manager获取所有节点并绘制火焰图
            try:
                state_manager = RuntimeStateManager.instance()
                if state_manager:
                    nodes = state_manager.query_by_task(task_id)
                    if nodes:
                        os.makedirs(f"logs/flame_graphs/{batch_id}", exist_ok=True)
                        flame_graph_path = f"logs/flame_graphs/{batch_id}/flame_{task_id}_{cur_time}.html"
                        from train.examples.train_gaia_with_aworld_verl.log_processor.analyze_state_manager import \
                            plot_flame_graph
                        plot_flame_graph(nodes, task_id, flame_graph_path)
            except Exception as flame_err:
                logging.warning(f"绘制火焰图失败: {flame_err}, trace: {traceback.format_exc()}")
            
            if isinstance(result, TaskResponse):
                return {"answer": result[task_id].answer, "trajectory": result[task_id].trajectory}
            if isinstance(result, dict):
                task_result = result[task_id]
                eval_digest_logger.info(
                    f"eval_task_digest|{batch_id}|{task_id}|{task_result.time_cost:0.1f}|{task_result.usage}")
                return {"answer": task_result.answer, "trajectory": task_result.trajectory}
            else:
                return {"answer": result}
        except Exception as err:
            print(f"err is {err}, trace is {traceback.format_exc()}")
            return {"answer": str(err)}
