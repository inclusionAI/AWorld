import requests
import aworld.trace as trace
from aworld.core.tool.base import Tool, AgentInput, ToolFactory
from examples.tools.tool_action import GetTraceAction
from aworld.tools.utils import build_observation
from aworld.config.conf import ToolConfig
from aworld.core.common import Observation, ActionModel, ActionResult
from typing import Tuple, Dict, Any, List
from aworld.logs.util import logger


@ToolFactory.register(name="trace",
                      desc="Get the trace of the current execution.",
                      supported_action=GetTraceAction,
                      conf_file_name=f'trace_tool.yaml')
class TraceTool(Tool):
    def __init__(self,
                 conf: ToolConfig,
                 **kwargs) -> None:
        """
        Initialize the TraceTool
        Args:
            conf: tool config
            **kwargs: -
        Return:
            None
        """
        super(TraceTool, self).__init__(conf, **kwargs)
        self.type = "function"
        self.trace_server_port = self.conf.get('trace_server_port')

    def reset(self,
              *,
              seed: int | None = None,
              options: Dict[str, str] | None = None) -> Tuple[AgentInput, dict[str, Any]]:
        """
        Reset the executor
        Args:
            seed: -
            options: -
        Returns:
            AgentInput, dict[str, Any]: -
        """
        self._finished = False
        return build_observation(observer=self.name(),
                                 ability=GetTraceAction.GET_TRACE.value.name), {}

    def close(self) -> None:
        """
        Close the executor
        Returns:
            None
        """
        self._finished = True

    def do_step(self,
                actions: List[ActionModel],
                **kwargs) -> Tuple[Observation, float, bool, bool, dict[str, Any]]:
        reward = 0
        fail_error = ""
        observation = build_observation(observer=self.name(),
                                        ability=GetTraceAction.GET_TRACE.value.name)
        results = []
        try:
            if not actions:
                return (observation, reward,
                        kwargs.get("terminated",
                                   False), kwargs.get("truncated", False), {
                            "exception": "actions is empty"
                        })
            for action in actions:
                trace_id = action.params.get("trace_id", "")
                if not trace_id:
                    current_span = trace.get_current_span()
                    if current_span:
                        trace_id = current_span.get_trace_id()
                if not trace_id:
                    logger.warning(f"{action} no trace_id to fetch.")
                    continue
                try:
                    trace_data = self.fetch_trace_data(trace_id)
                    error = ""
                except Exception as e:
                    error = str(e)
                results.append(trace_data)
                observation.action_result.append(
                    ActionResult(is_done=True,
                                 success=False if error else True,
                                 content=f"{trace_data}",
                                 error=f"{error}",
                                 keep=False))

            observation.content = f"{results}"
            reward = 1
        except Exception as e:
            fail_error = str(e)
        finally:
            self._finished = True

        info = {"exception": fail_error}
        info.update(kwargs)
        return (observation, reward, kwargs.get("terminated", False),
                kwargs.get("truncated", False), info)

    def fetch_trace_data(self, trace_id=None):
        '''
            fetch trace data from trace server.
            return trace data, like:
            {
                'trace_id': trace_id,
                'root_span': [],
            }
        '''
        try:
            if trace_id:
                response = requests.get(
                    f'http://localhost:7079/api/traces/{trace_id}')
                response.raise_for_status()
                return response.json() or {"trace_id": trace_id, "root_span": []}
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching trace data: {e}")
            return {"trace_id": trace_id, "root_span": []}
