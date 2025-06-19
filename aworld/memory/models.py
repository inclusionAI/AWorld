from pydantic import BaseModel, ConfigDict, Field
from aworld.core.memory import MemoryItem
from typing import Any, Dict, List, Optional, Literal

from aworld.models.model_response import ToolCall

class MessageMetadata(BaseModel):
    """
    Metadata for memory messages, including user, session, task, and agent information.
    Args:
        user_id (str): The ID of the user.
        session_id (str): The ID of the session.
        task_id (str): The ID of the task.
        agent_id (str): The ID of the agent.
    """
    user_id: str = Field(description="The ID of the user")
    session_id: str = Field(description="The ID of the session")
    task_id: str = Field(description="The ID of the task")
    agent_id: str = Field(description="The ID of the agent")

    model_config = ConfigDict(extra="allow")

    @property
    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()

class AgentExperienceItem(BaseModel):
    skill: str = Field(description="The skill demonstrated in the experience")
    actions: List[str] = Field(description="The actions taken by the agent")

class AgentExperience(MemoryItem):
    """
    Represents an agent's experience, including skills and actions.
    All custom attributes are stored in content and metadata.
    Args:
        agent_id (str): The ID of the agent.
        skill (str): The skill demonstrated in the experience.
        actions (List[str]): The actions taken by the agent.
        metadata (Optional[Dict[str, Any]]): Additional metadata.
    """
    def __init__(self, agent_id: str, skill: str, actions: List[str], metadata: Optional[Dict[str, Any]] = None) -> None:
        meta = metadata.copy() if metadata else {}
        meta['agent_id'] = agent_id
        agent_experience = AgentExperienceItem(skill=skill, actions=actions)
        super().__init__(content=agent_experience, metadata=meta, memory_type="agent_experience")

    @property
    def agent_id(self) -> str:
        return self.metadata['agent_id']

    @property
    def skill(self) -> str:
        return self.content.skill

    @property
    def actions(self) -> List[str]:
        return self.content.actions

class UserProfileItem(BaseModel):
    key: str = Field(description="The key of the profile")
    value: Any = Field(description="The value of the profile")

class UserProfile(MemoryItem):
    """
    Represents a user profile key-value pair.
    All custom attributes are stored in content and metadata.
    Args:
        user_id (str): The ID of the user.
        key (str): The profile key.
        value (Any): The profile value.
        metadata (Optional[Dict[str, Any]]): Additional metadata.
    """
    def __init__(self, user_id: str, key: str, value: Any, metadata: Optional[Dict[str, Any]] = None) -> None:
        meta = metadata.copy() if metadata else {}
        meta['user_id'] = user_id
        user_profile = UserProfileItem(key=key, value=value)
        super().__init__(content=user_profile, metadata=meta, memory_type="user_profile")

    @property
    def user_id(self) -> str:
        return self.metadata['user_id']

    @property
    def key(self) -> str:
        return self.content.key

    @property
    def value(self) -> Any:
        return self.content.value

class MemoryMessage(MemoryItem):
    """
    Represents a memory message with role, user, session, task, and agent information.
    Args:
        role (str): The role of the message sender.
        metadata (MessageMetadata): Metadata object containing user, session, task, and agent IDs.
        content (Optional[Any]): Content of the message.
    """
    def __init__(self, role: str, metadata: MessageMetadata, content: Optional[Any] = None) -> None:
        meta = metadata.to_dict()
        meta['role'] = role
        super().__init__(content=content, metadata=meta, memory_type="message")

    @property
    def role(self) -> str:
        return self.metadata['role']

    @property
    def user_id(self) -> str:
        return self.metadata['user_id']

    @property
    def session_id(self) -> str:
        return self.metadata['session_id']

    @property
    def task_id(self) -> str:
        return self.metadata['task_id']

    @property
    def agent_id(self) -> str:
        return self.metadata['agent_id']

class SystemMessage(MemoryMessage):
    """
    Represents a system message with role and content.
    Args:
        metadata (MessageMetadata): Metadata object containing user, session, task, and agent IDs.
        content (str): The content of the message.
    """
    def __init__(self, content: str, metadata: MessageMetadata) -> None:
        super().__init__(role="system", metadata=metadata, content=content)

    @property
    def content(self) -> str:
        return self._content

class HumanMessage(MemoryMessage):
    """
    Represents a human message with role and content.
    Args:
        metadata (MessageMetadata): Metadata object containing user, session, task, and agent IDs.
        content (str): The content of the message.
    """
    def __init__(self, metadata: MessageMetadata, content: str) -> None:
        super().__init__(role="human", metadata=metadata, content=content)

    @property
    def content(self) -> str:
        return self._content

class AIMessage(MemoryMessage):
    """
    Represents an AI message with role and content.
    Args:
        metadata (MessageMetadata): Metadata object containing user, session, task, and agent IDs.
        content (str): The content of the message.
    """
    def __init__(self, content: str, tool_calls: List[ToolCall], metadata: MessageMetadata) -> None:
        meta = metadata.to_dict()
        meta['tool_calls'] = [tool_call.to_dict() for tool_call in tool_calls]
        super().__init__(role="assistant", metadata=MessageMetadata(**meta), content=content)

    @property
    def content(self) -> str:
        return self._content
    
    @property
    def tool_calls(self) -> List[ToolCall]:
        return [ToolCall(**tool_call) for tool_call in self.metadata['tool_calls']]

class ToolMessage(MemoryMessage):
    """
    Represents a tool message with role, content, tool_call_id, and status.
    Args:
        metadata (MessageMetadata): Metadata object containing user, session, task, and agent IDs.
        tool_call_id (str): The ID of the tool call.
        status (Literal["success", "error"]): The status of the tool call.
        content (str): The content of the message.
    """
    def __init__(self, tool_call_id: str, content: str, status: Literal["success", "error"] = "success", metadata: MessageMetadata = None) -> None:
        metadata.tool_call_id = tool_call_id
        metadata.status = status
        super().__init__(role="tool", metadata=metadata, content=content)

    @property
    def tool_call_id(self) -> str:
        return self.metadata['tool_call_id']

    @property
    def status(self) -> str:
        return self.metadata['status']

    @property
    def content(self) -> str:
        return self._content