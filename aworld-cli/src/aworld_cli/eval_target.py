"""
CLI EvalTarget implementation for aworld-cli batch evaluation.

This module provides a generic EvalTarget that can be used with aworld-cli eval command.
It supports both local and remote agents using executor pattern.
"""
import os
import traceback
from datetime import datetime
from typing import Optional, Dict, Any

from aworld.evaluations.base import EvalTarget, EvalDataCase
from aworld.logs.util import logger


class AWorldCliEvalTarget(EvalTarget):
    """
    Generic EvalTarget for aworld-cli evaluation command.
    
    This class provides a flexible evaluation target that can work with both
    local and remote agents using executor pattern, similar to continuous.py.
    
    Example:
        ```python
        eval_target = AWorldCliEvalTarget(
            agent_name="PPTTeam",
            remote_backend="http://localhost:8000",
            query_column="query"
        )
        ```
    """
    
    def __init__(
        self,
        agent_name: str,
        remote_backend: Optional[str] = None,
        query_column: str = "query",
        output_dir: Optional[str] = None,
    ):
        """
        Initialize AWorldCliEvalTarget.
        
        Args:
            agent_name: Name of the agent to use for evaluation.
            remote_backend: Optional remote backend URL. If None, uses local agent.
            query_column: Column name in CSV/data that contains the query/task content.
            output_dir: Directory to save evaluation results. If None, uses current directory.
        """
        super().__init__()
        self.agent_name = agent_name
        self.remote_backend = remote_backend
        self.query_column = query_column
        self.output_dir = output_dir or os.getcwd()
        
        # Setup logging
        self._setup_logging()
    
    def _setup_logging(self) -> None:
        """Setup logging for evaluation digest using aworld logger."""
        log_dir = os.path.join(self.output_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "eval_digest.log")
        
        # Use aworld logger with file handler
        from aworld.logs.util import AWorldLogger
        self.eval_digest_logger = AWorldLogger(
            tag='EvalDigest',
            name='AWorldCLI',
            formatter="<black>{time:YYYY-MM-DD HH:mm:ss.SSS} | eval_digest | {level} |</black> <level>{message}</level>",
            disable_console=True  # Disable console output for digest logger
        )
        # Add custom file handler for digest log with rotation
        # Note: loguru's retention uses time units (e.g., "10 days"), not file counts
        # For file count limiting, we use a longer retention period
        self.eval_digest_logger._logger.add(
            log_path,
            rotation="30 MB",
            retention="30 days",  # Keep logs for 30 days (loguru doesn't support file count directly)
            encoding="utf-8",
            level="INFO",
            filter=lambda record: record['extra'].get('name') == 'EvalDigest',
            format="<black>{time:YYYY-MM-DD HH:mm:ss.SSS} | eval_digest | {level} |</black> <level>{message}</level>"
        )
    
    async def _create_executor(self, session_id: str):
        """
        Create a new executor instance for the given session_id.
        Each evaluation case should have its own executor with independent session.
        
        Args:
            session_id: Session ID for the executor.
            
        Returns:
            AgentExecutor instance.
        """
        from .runtime.mixed import MixedRuntime
        from .models import AgentInfo
        from .executors.local import LocalAgentExecutor
        from .executors.remote import RemoteAgentExecutor
        from aworld.core.context.amni import TaskInput, ApplicationContext
        from aworld.core.context.amni.config import AmniConfigFactory, AmniConfigLevel
        from datetime import datetime
        
        # Load agents using MixedRuntime
        runtime = MixedRuntime(remote_backends=[self.remote_backend] if self.remote_backend else None)
        all_agents = await runtime._load_agents()
        
        # Find the requested agent
        selected_agent = None
        for agent in all_agents:
            if agent.name == self.agent_name:
                selected_agent = agent
                break
        
        if not selected_agent:
            raise ValueError(f"Agent '{self.agent_name}' not found")
        
        # Create executor with specific session_id
        source_info = runtime._agent_sources.get(selected_agent.name)
        if not source_info:
            raise ValueError(f"Source information not found for agent '{self.agent_name}'")
        
        if source_info["type"] == "local":
            # Create local executor with specific session_id
            source = source_info["source"]
            context_config = AmniConfigFactory.create(
                AmniConfigLevel.NAVIGATOR,
                debug_mode=True
            )
            context_config.agent_config.history_scope = "session"
            
            # Get swarm
            try:
                swarm = await source.get_swarm(None)
            except (TypeError, AttributeError):
                # If swarm function requires context, create a temporary context
                temp_task_input = TaskInput(
                    user_id="eval_user",
                    session_id=session_id,
                    task_id=f"temp_task_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                    task_content="",
                    origin_user_input=""
                )
                temp_context = await ApplicationContext.from_input(
                    temp_task_input,
                    context_config=context_config
                )
                swarm = await source.get_swarm(temp_context)
            
            # Create executor with specific session_id
            executor = LocalAgentExecutor(
                swarm=swarm,
                context_config=context_config,
                console=None,  # No console output for batch evaluation
                session_id=session_id  # Use the provided session_id
            )
        elif source_info["type"] == "remote":
            # Create remote executor with specific session_id
            backend_url = source_info["location"]
            executor = RemoteAgentExecutor(
                backend_url=backend_url,
                agent_name=self.agent_name,
                console=None  # No console output for batch evaluation
            )
            # Set session_id for remote executor
            executor.session_id = session_id
        else:
            raise ValueError(f"Unknown source type for agent '{self.agent_name}'")
        
        return executor
    
    def _extract_query(self, case_data: dict) -> str:
        """
        Extract query/task content from case data.
        
        Args:
            case_data: Dictionary containing evaluation case data.
            
        Returns:
            Query string to execute.
        """
        # Try to get query from specified column
        query = case_data.get(self.query_column)
        self.eval_digest_logger.info(f"Extracting query from case data: {case_data}")
        if query:
            return str(query)
        
        # Fallback: try common column names
        for col in ["query", "question", "prompt", "input", "task"]:
            if col in case_data:
                return str(case_data[col])
        
        # Last resort: use the entire case_data as string
        logger.warning(f"Could not find query column '{self.query_column}' in case data. Using first value.")
        if case_data:
            return str(list(case_data.values())[0])
        
        raise ValueError(f"No query found in case data. Available keys: {list(case_data.keys())}")
    
    async def predict(self, index: int, o_input: EvalDataCase[dict]) -> dict:
        """
        Execute prediction for a single evaluation case using executor.
        
        Args:
            index: Index of the evaluation case.
            o_input: Evaluation data case containing input data.
            
        Returns:
            Dictionary containing prediction result with 'answer' key.
        """
        # Handle both EvalDataCase object and dict input for robustness
        if isinstance(o_input, dict):
            # If input is a dict (serialized EvalDataCase), extract fields
            batch_id = o_input.get('run_id') or "default_batch"
            # case_data contains the actual query data
            input_data = o_input.get('case_data', {})
            eval_case_id = o_input.get('eval_case_id', str(index))
        else:
            # If input is EvalDataCase object, extract attributes
            batch_id = getattr(o_input, 'run_id', None) or "default_batch"
            input_data = getattr(o_input, 'case_data', {}) if hasattr(o_input, 'case_data') else {}
            eval_case_id = getattr(o_input, 'eval_case_id', str(index))
        
        # Extract query from case data
        # input_data is the actual case_data (dict) containing the query
        try:
            query = self._extract_query(input_data)
        except ValueError as e:
            logger.error(f"Error extracting query from case {index}: {e}")
            return {"answer": f"Error: {str(e)}"}
        
        # Generate session ID
        case_id = input_data.get("id", str(index)) if isinstance(input_data, dict) else str(index) if isinstance(input_data, dict) else str(index)
        session_id = f"{batch_id}_session#{case_id}"
        task_id = f"{batch_id}_task#{case_id}"
        
        try:
            # Create executor for this session and execute chat
            executor = await self._create_executor(session_id)
            start_time = datetime.now()
            
            # Execute using executor's chat method (similar to continuous.py)
            answer = await executor.chat(query)
            
            end_time = datetime.now()
            time_cost = (end_time - start_time).total_seconds()
            
            # Log evaluation digest
            self.eval_digest_logger.info(
                f"eval_task_digest|{batch_id}|{task_id}|{time_cost:.1f}|N/A"
            )
            
            # Save results
            self._save_results(batch_id, task_id, index, eval_case_id, answer)
            
            return {"answer": answer}
            
        except Exception as err:
            error_msg = f"Error in prediction: {str(err)}"
            logger.error(f"Error in case {index}: {error_msg}\n{traceback.format_exc()}")
            return {"answer": error_msg}
    
    def _save_results(
        self,
        batch_id: str,
        task_id: str,
        index: int,
        eval_case_id: str,
        answer: str,
    ) -> None:
        """
        Save evaluation results to files.
        
        Args:
            batch_id: Batch ID for grouping results.
            task_id: Task ID.
            index: Case index.
            eval_case_id: Evaluation case ID.
            answer: Answer string from executor.
        """
        try:
            # Save answer
            results_dir = os.path.join(self.output_dir, "results", batch_id)
            os.makedirs(results_dir, exist_ok=True)
            cur_time = datetime.now().strftime('%Y%m%d%H%M%S')
            result_file = os.path.join(results_dir, f"{task_id}_{cur_time}_{eval_case_id}.txt")
            
            with open(result_file, "w", encoding="utf-8") as f:
                f.write(answer)
                    
        except Exception as e:
            logger.warning(f"Failed to save results for case {index}: {e}")

