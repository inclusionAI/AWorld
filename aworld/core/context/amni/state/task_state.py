from typing import Optional, Dict, Literal, OrderedDict

from pydantic import BaseModel, Field
from pydantic import field_validator

from .agent_state import ApplicationAgentState
from .common import WorkingState, TaskInput, TaskOutput
from ..utils.modelplus import from_dict_to_memory_message
from aworld.config import ConfigDict
from aworld.core.context.base import ContextUsage
from aworld.memory.models import MemoryMessage


class SubTask(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    task_id: str
    status: Literal['INIT', 'PROCESSING', 'SUCCESS', 'FAILED']
    input: TaskInput
    result: Optional[TaskOutput] = Field(default=None)

    @classmethod
    def from_task_input(cls, input: TaskInput):
        return cls(
            task_id=input.task_id,
            status='INIT',
            input=input
        )

    @property
    def user_input(self):
        return self.input.origin_user_input

    def to_dict(self):
        """Convert SubTask object to JSON serializable dictionary"""
        return {
            "task_id": self.task_id,
            "status": self.status,
            "input": self.input.model_dump() if hasattr(self.input, 'model_dump') else str(self.input),
            "result": self.result,
        }

    @property
    def desc(self) -> str:
        """
        Generate a clear description for LLM understanding
        
        Returns:
            str: Formatted task description
        """
        return f"""
----------------------------------
Task: {self.input.task_content.strip()}
Status: {self.status}
Result: {self.result if self.result is not None else 'No result yet'}
----------------------------------
"""


class TaskWorkingState(WorkingState):

    # task_status
    status: Literal['INIT', 'PROCESSING', 'SUCCESS', 'FAILED'] = Field(default='INIT', description="task status")

    # sub tasks
    sub_task_list: Optional[list[SubTask]] = Field(default_factory=list, description="sub task list")

    # Agent context isolation
    agent_states: Optional[Dict[str, ApplicationAgentState]] = Field(default_factory=dict,
                                                                     description="Agent context isolation",
                                                                     exclude=True)
    # Task Core Step: Record some necessary steps,
    # core_steps: Optional[list[str]] = Field(default_factory=list)

    def set_agent_state(self, agent_id: str, application_agent_state: ApplicationAgentState):
        self.agent_states[agent_id] = application_agent_state

    def has_agent_state(self, agent_id: str):
        return agent_id in self.agent_states

    def get_agent_state(self, agent_id: str) -> Optional[ApplicationAgentState]:
        return self.agent_states.get(agent_id)
        
    def upsert_subtask_by_input(self, sub_task_input: TaskInput) -> None:
        """
        Update or insert a subtask into the sub_task_list.

        If the subtask ID already exists, update the corresponding subtask; 
        if it does not exist, add a new subtask.

        Args:
            sub_task_input (TaskInput): The input information of the subtask
        """
        has_existed = False
        for i, sub_task in enumerate(self.sub_task_list):
            if sub_task.task_id == sub_task_input.task_id:
                has_existed = True
                self.sub_task_list[i] = SubTask.from_task_input(sub_task_input)
                break
        if not has_existed:
            self.sub_task_list.append(SubTask.from_task_input(sub_task_input))


class BaseTaskContextState(BaseModel):
    """
    Base class for task context management in the AMNI context system.
    
    This class provides the fundamental structure for managing task execution context,
    including input parameters, working state, output results, and task history.
    It serves as the foundation for building more specialized task context classes.
    
    Attributes:
        task_input: Current task input parameters and requirements
        working_state: Working memory for current task execution state and intermediate results
        task_output: Final result and output of the current task execution
        previous_round_results: Previous rounds results of current task (for multi-round execution)
        parent_task: Parent task input that spawned this current task (for hierarchical structures)
        context_usage: Context usage information and metadata
    """

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    # Current task input parameters and requirements
    task_input: Optional[TaskInput] = Field(description="Current task input parameters and requirements")

    # Working memory: Current task execution state and intermediate results, customizable for specific needs
    working_state: Optional[TaskWorkingState] = Field(description="Working memory: Current task execution state and intermediate results")

    # Final result and output of the current task execution
    task_output: Optional[TaskOutput] = Field(default=None, description="Final result and output of the current task execution")

    # Previous rounds results of current task (for multi-host task execution or cross-session task execution)
    previous_round_results: Optional[list[MemoryMessage]] = Field(default_factory=list, description="Previous rounds results of current task", exclude=True)

    @field_validator("previous_round_results", mode="before")
    @classmethod
    def validate_previous_round_results(cls, v):
        if v is None:
            return []
        if isinstance(v, list):
            result = []
            for item in v:
                if isinstance(item, dict):
                    message = from_dict_to_memory_message(item)
                    if message:
                        result.append(message)
                elif isinstance(item, MemoryMessage):
                    result.append(item)
            return result
        return []

    # Parent task input that spawned this current task (for hierarchical task structures)
    parent_task: Optional[TaskInput] = Field(default=None, description="Parent task input that spawned this current task")

    # context_usage
    context_usage: Optional[ContextUsage] = Field(default=None, description="ContextUsage")

    def set_task_input(self, task_input: TaskInput):
        self.task_input = task_input

    def set_task_output(self, task_output: TaskOutput):
        self.task_output = task_output

    def set_agent_state(self, agent_id: str, agent_state: ApplicationAgentState):
        self.working_state.set_agent_state(agent_id, agent_state)

    def get_agent_state(self, agent_id: str) -> Optional[ApplicationAgentState]:
        return self.working_state.get_agent_state(agent_id)

    def has_agent_state(self, agent_id: str):
        return self.working_state.has_agent_state(agent_id)


class ApplicationTaskContextState(BaseTaskContextState):

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)
