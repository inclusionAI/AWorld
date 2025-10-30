import uuid
from typing import Dict, Any, Optional, List, Union

from pydantic import BaseModel, Field

from aworld.config import ConfigDict
from aworld.memory.models import Fact, MemoryMessage, MemorySummary, UserProfile
from aworld.output import Artifact


class OpenAIChatMessage(BaseModel):
    """
    OpenAI chat message model
    
    Represents a single message in a chat conversation with role and content.
    Follows OpenAI's chat completion API message format.
    
    Attributes:
        role (str): Role of the message sender (e.g., 'user', 'assistant', 'system')
        content (str | List): Content of the message, can be string or list format
    """
    role: str
    content: str | List

    model_config = ConfigDict(extra="allow")


class OpenAIChatCompletionForm(BaseModel):
    """
    OpenAI chat completion request form
    
    Base model for OpenAI chat completion API requests, including streaming,
    model selection, and message history.
    
    Attributes:
        stream (bool): Whether to stream the response, defaults to True
        model (Optional[str]): Model identifier to use for completion
        messages (Optional[List[OpenAIChatMessage]]): List of conversation messages
    """
    stream: bool = True
    model: Optional[str] = Field(default=None)
    messages: Optional[list[OpenAIChatMessage]] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class ContextUsage(BaseModel):
    total_context_length: int = Field(default=0)
    used_context_length: int = Field(default=0)

class TaskInput(OpenAIChatCompletionForm):
    """
    Task input class, extending OpenAI Chat Completion protocol
    
    Inherits from OpenAIChatCompletionForm and adds additional fields required for task execution,
    including user identification, session management, task content, etc. Supports subtask creation
    and agent identification.
    
    Attributes:
        user_id (str): Unique user identifier
        session_id (str): Unique session identifier
        task_id (str): Unique task identifier
        task_content (Union[Optional[str], list[OpenAIChatMessage]]): Task input content, supports string or message list
        origin_user_input (Union[Optional[str], list[OpenAIChatMessage]]): Original user input content
    """
    user_id: str
    session_id: str
    task_id: str

    # Task input content
    task_content: Union[Optional[str], list[OpenAIChatMessage]]

    # Original user input content
    origin_user_input: Union[Optional[str], list[OpenAIChatMessage]]
    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    @property
    def agent_id(self) -> str:
        """
        Get agent identifier
        
        Returns:
            str: Model name as agent ID
        """
        return self.model

    def new_subtask(self, sub_task_content: str, sub_task_id: str = None) -> "TaskInput":
        """
        Create new subtask input
        
        Creates a new subtask based on the current task, inheriting user ID and session ID,
        but using new task ID and content.
        
        Args:
            sub_task_content (str): Content of the subtask
            sub_task_id (str, optional): Subtask ID, auto-generated if not provided
            
        Returns:
            TaskInput: New subtask input object
        """
        if not sub_task_id:
            sub_task_id = f"{self.task_id}_sub_{str(uuid.uuid1().hex)}"
        sub_task_input = TaskInput(
            user_id=self.user_id,
            session_id=self.session_id,
            task_id=sub_task_id,
            task_content=sub_task_content,
            origin_user_input=sub_task_content,
        )
        return sub_task_input


class ContextFileManager(BaseModel):
    """
    Manages file artifacts and their descriptions in the context.

    This class provides functionality to store, retrieve, update, and manage
    file artifacts with their associated descriptions. It maintains a mapping
    between artifact IDs and their descriptions for easy access and management.
    """

    files: Optional[Dict[str, Optional[str]]] = Field(
        default_factory=dict,
        description="Collection of output artifacts/files with their descriptions"
    )

    def add_file(self, artifact_id: str, artifact_desc: Optional[str]) -> None:
        """
        Add a new file artifact to the output collection.

        This method stores a new artifact with its description in the files dictionary.
        If an artifact with the same ID already exists, it will be overwritten.

        Args:
            artifact_id (str): Unique identifier for the artifact
            artifact_desc (Optional[str]): Description of the artifact content, can be None

        Returns:
            None
        """
        self.files[artifact_id] = artifact_desc

    def get_file_by_id(self, artifact_id: str) -> Optional[str]:
        """
        Get file description by artifact ID

        Args:
            artifact_id: Unique identifier for the artifact

        Returns:
            Artifact description if found, None otherwise
        """
        return self.files.get(artifact_id)

    def file_index(self) -> Dict[str, Optional[str]]:
        """
        Get the complete file index

        Returns:
            Dictionary mapping artifact IDs to their descriptions (can be None)
        """
        if not self.files:
            return {}
        return self.files.copy()

    def file_content(self):
        pass

    def remove_file(self, artifact_id: str) -> bool:
        """
        Remove a file artifact by ID

        Args:
            artifact_id: Unique identifier for the artifact

        Returns:
            True if file was removed, False if not found
        """
        if artifact_id in self.files:
            del self.files[artifact_id]
            return True
        return False

    def update_file(self, artifact_id: str, artifact_desc: Optional[str]) -> bool:
        """
        Update an existing file artifact description

        Args:
            artifact_id: Unique identifier for the artifact
            artifact_desc: New description of the artifact content, can be None

        Returns:
            True if file was updated, False if not found
        """
        if artifact_id in self.files:
            self.files[artifact_id] = artifact_desc
            return True
        return False

    def clear_files(self) -> None:
        """
        Clear all file artifacts
        """
        self.files.clear()

    def get_file_count(self) -> int:
        """
        Get the total number of file artifacts

        Returns:
            Number of file artifacts
        """
        return len(self.files)



class WorkingState(ContextFileManager):
    """
    [Runtime]Working memory state container for runtime

    Stores temporary data and intermediate results during task execution.
    This class serves as a placeholder for working memory implementation.
    """

    # short-term memory: conversations
    history_messages: Optional[list[MemoryMessage]] = Field(default_factory=list, exclude=True)

    # short-term memory： summary
    summaries: Optional[list[MemorySummary]] = Field(default_factory=list)

    # importance facts share in current task
    facts: Optional[list[Fact]] = Field(default_factory=list, description="Relation Facts")

    # user profile
    user_profiles: Optional[list[UserProfile]] = Field(default_factory=list, description="")

    # custom info
    kv_store: Optional[Dict[str, Any]] = Field(default_factory=dict, description="custom_info")

    def get_knowledge(self, knowledge_id: str) -> Optional[str]:
        return self.files.get(knowledge_id)

    @property
    def knowledge_index(self) -> dict:
        """
        Convert knowledge list to a dictionary with artifact_id as key and summary as value
        
        Returns:
            dict: knowledge_index
        """
        return self.file_index()


    def save_knowledge(self, knowledge: Artifact) -> None:
        """
        Save a single artifact to the working state.
        
        If the artifact already exists (based on ID), it will be updated.
        Otherwise, it will be added as a new artifact.
        
        Args:
            knowledge (Artifact): The artifact to save or update
        """
        self.add_file(knowledge.artifact_id, knowledge.summary)

    def save_knowledge_list(self, knowledge_list: list[Artifact]) -> None:
        """
        Save multiple artifacts to the working state.
        
        Each artifact will be processed individually using save_artifact method,
        which handles both adding new artifacts and updating existing ones.
        
        Args:
            knowledge_list (list[Artifact]): List of artifacts to save
        """
        for item in knowledge_list:
            self.save_knowledge(item)


    model_config = ConfigDict(extra="allow")

class TaskOutput(ContextFileManager):
    """
    Task output state container

    Stores task execution results including files and final output
    """

    # cooperate info for cooperate
    todo_info: Optional[str] = Field(default=None, description="todo plan info")

    actions_info: Optional[str] = Field(default=None, description="cooperate info")

    # left steps result
    result: Optional[str] = Field(default=None, description="task final result")


class TaskHistoryItem(BaseModel):
    """
    a common task history item model
    """
    task_input: TaskInput = Field(default=None, description="task input")
    task_output: TaskOutput = Field(default=None, description="task output")

class Summary(BaseModel):
    """
    a common summary model for task output and conversation summary
    """
    summary_content: str = Field(default=None, description="summary")