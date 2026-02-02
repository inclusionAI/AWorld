"""
CLI EvalTarget implementation for batch evaluation.

This module provides a generic EvalTarget that can be used with CLI eval command.
It supports custom swarm building functions and uses executor pattern for task execution.
"""
import os
import logging
import traceback
from datetime import datetime
from typing import Optional, Callable, Any

from aworld.core.context.amni.config import AmniConfigFactory, AmniConfigLevel
from aworld.evaluations.base import EvalTarget, EvalDataCase
from aworld.core.agent.swarm import Swarm

logger = logging.getLogger(__name__)


class CliEvalTarget(EvalTarget):
    """
    Generic EvalTarget for CLI evaluation command.
    
    This class provides a flexible evaluation target that can work with different
    swarm configurations. It uses executor pattern for task execution, similar to
    aworld-cli executors, avoiding the need to build Task objects directly.
    
    Example:
        ```python
        def build_my_swarm():
            return Swarm(...)
        
        eval_target = CliEvalTarget(
            swarm_builder=build_my_swarm,
            query_column="query"
        )
        ```
    """
    
    def __init__(
        self,
        swarm_builder: Optional[Callable[[], Swarm]] = None,
        query_column: str = "query",
        output_dir: Optional[str] = None,
        context_config_level: AmniConfigLevel = AmniConfigLevel.NAVIGATOR,
    ):
        """
        Initialize CliEvalTarget.
        
        Args:
            swarm_builder: Function that returns a Swarm instance. If None, will try to use default.
            query_column: Column name in CSV/data that contains the query/task content.
            output_dir: Directory to save evaluation results. If None, uses current directory.
            context_config_level: Context configuration level for ApplicationContext.
        """
        super().__init__()
        self.swarm_builder = swarm_builder
        self.query_column = query_column
        self.output_dir = output_dir or os.getcwd()
        self.context_config_level = context_config_level
        self._swarm = None  # Cached swarm instance
        self._context_config = None  # Cached context config
        
        # Setup logging
        self._setup_logging()
    
    def _setup_logging(self) -> None:
        """Setup logging for evaluation digest."""
        log_dir = os.path.join(self.output_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "eval_digest.log")
        
        from logging.handlers import RotatingFileHandler
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=30 * 1024 * 1024,  # 30MB per file
            backupCount=10,  # Keep 10 backup files
            encoding='utf-8'
        )
        self.eval_digest_logger = logging.getLogger("eval_digest")
        self.eval_digest_logger.setLevel(logging.INFO)
        self.eval_digest_logger.addHandler(file_handler)
    
    def _get_swarm(self) -> Swarm:
        """
        Get or create swarm instance (cached).
        
        Returns:
            Swarm instance.
        """
        if self._swarm is None:
            if self.swarm_builder:
                self._swarm = self.swarm_builder()
            else:
                # Try to use default swarm builder
                try:
                    from examples.xbench.agents.swarm import build_xbench_swarm
                    self._swarm = build_xbench_swarm()
                except ImportError:
                    logger.error("No swarm_builder provided and default not available. Please provide swarm_builder.")
                    raise ValueError("swarm_builder is required")
        return self._swarm
    
    def _get_context_config(self):
        """
        Get or create context config (cached).
        
        Returns:
            Context config instance.
        """
        if self._context_config is None:
            self._context_config = AmniConfigFactory.create(self.context_config_level)
        return self._context_config
    
    def _create_executor(self, session_id: str):
        """
        Create a new executor instance for the given session_id.
        
        Args:
            session_id: Session ID for the executor.
            
        Returns:
            AgentExecutor instance.
        """
        swarm = self._get_swarm()
        context_config = self._get_context_config()
        
        # Import and create LocalAgentExecutor
        try:
            from aworld.experimental.aworld_cli.executors.local import LocalAgentExecutor
            return LocalAgentExecutor(
                swarm=swarm,
                context_config=context_config,
                console=None,  # No console output for batch evaluation
                session_id=session_id
            )
        except ImportError:
            logger.error("LocalAgentExecutor not available. Please ensure aworld-cli is properly installed.")
            raise ImportError("LocalAgentExecutor is required but not available")
    
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
        batch_id = o_input.run_id or "default_batch"
        input_data = o_input.case_data
        
        # Extract query from case data
        try:
            query = self._extract_query(input_data)
        except ValueError as e:
            logger.error(f"Error extracting query from case {index}: {e}")
            return {"answer": f"Error: {str(e)}"}
        
        # Generate session ID
        case_id = input_data.get("id", str(index))
        session_id = f"{batch_id}_session#{case_id}"
        task_id = f"{batch_id}_task#{case_id}"
        
        try:
            # Create executor for this session and execute chat
            executor = self._create_executor(session_id)
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
            self._save_results(batch_id, task_id, index, o_input.eval_case_id, answer)
            
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

