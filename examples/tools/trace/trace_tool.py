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
        self.get_trace_url = self.conf.get('get_trace_url')

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
                    observation.action_result.append(
                        ActionResult(is_done=True,
                                     success=False,
                                     content="",
                                     error="no trace_id to fetch",
                                     keep=False))
                    continue
                try:
                    trace_data = self.fetch_trace_data(trace_id)
                    logger.info(f"trace_data={trace_data}")
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
                url = self.get_trace_url or "http://localhost:7079/api/traces"
                response = requests.get(f'{url.rstrip("/")}/{trace_id}')
                response.raise_for_status()
                logger.info(f"response={response.json()}")
                if response:
                    return self.proccess_trace(response.json())
                return {"trace_id": trace_id, "root_span": []}
        except Exception as e:
            logger.error(f"Error fetching trace data: {e}")
            return {"trace_id": trace_id, "root_span": []}

    def proccess_trace(self, trace_data):
        root_spans = trace_data.get("root_span")
        for span in root_spans:
            self.choose_attribute(span)
        return trace_data

    def choose_attribute(self, span):
        include_attr = ["llm.completion_tokens",
                        "llm.prompt_tokens", "llm.total_tokens"]
        result_attributes = {}
        origin_attributes = span.get("attributes") or {}
        for key, value in origin_attributes.items():
            if key in include_attr:
                result_attributes[key] = value
        span["attributes"] = result_attributes
        if span.get("children"):
            for child in span.get("children"):
                self.choose_attribute(child)
