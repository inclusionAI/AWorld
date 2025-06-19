from pydantic import BaseModel, Field
from aworld.core.memory import MemoryItem
from typing import Any, Dict, List, Optional


class AgentExperienceItem(BaseModel):
    skill: str = Field(description="The skill demonstrated in the experience")
    actions: List[str] = Field(description="The actions taken by the agent")

class AgentExperience(MemoryItem):
    """
    Represents an agent's experience, including skills and actions.
    All custom attributes are stored in metadata.
    Args:
        agent_id (str): The ID of the agent.
        skill (str): The skill demonstrated in the experience.
        actions (List[str]): The actions taken by the agent.
        metadata (Optional[Dict[str, Any]]): Additional metadata.
        content (Optional[Any]): Content of the memory item.
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
    All custom attributes are stored in metadata.
    Args:
        user_id (str): The ID of the user.
        key (str): The profile key.
        value (Any): The profile value.
        metadata (Optional[Dict[str, Any]]): Additional metadata.
        content (Optional[Any]): Content of the memory item.
    """
    def __init__(self, user_id: str, key: str, value: Any, metadata: Optional[Dict[str, Any]] = None, content: Optional[Any] = None) -> None:
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
    Represents a memory message with role, user, task, and agent information.
    All custom attributes are stored in metadata.
    Args:
        role (str): The role of the message sender.
        user_id (str): The ID of the user.
        session_id (str): The ID of the session.
        task_id (str): The ID of the task.
        agent_id (str): The ID of the agent.
        metadata (Optional[Dict[str, Any]]): Additional metadata.
        content (Optional[Any]): Content of the message.
    """
    def __init__(self, role: str, user_id: str, session_id: str, task_id: str, agent_id: str, metadata: Optional[Dict[str, Any]] = None, content: Optional[Any] = None) -> None:
        meta = metadata.copy() if metadata else {}
        meta['role'] = role
        meta['user_id'] = user_id
        meta['session_id'] = session_id
        meta['task_id'] = task_id
        meta['agent_id'] = agent_id
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
    

class HumanMessage(MemoryMessage):
    """
    Represents a human message with role and content.
    All custom attributes are stored in metadata.
    Args:
        role (str): The role of the message sender.
        content (str): The content of the message.
        metadata (Optional[Dict[str, Any]]): Additional metadata.
    """
    def __init__(self, user_id: str, session_id: str, task_id: str, agent_id: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        meta = metadata.copy() if metadata else {}
        meta['role'] = "human"  
        super().__init__(role="human", user_id=user_id, session_id=session_id, task_id=task_id, agent_id=agent_id, metadata=meta, content=content)

    @property
    def content(self) -> str:
        return self.content

class AIMessage(MemoryMessage):
    """
    Represents an AI message with role and content.
    All custom attributes are stored in metadata.
    Args:
        role (str): The role of the message sender.
        content (str): The content of the message.
        metadata (Optional[Dict[str, Any]]): Additional metadata.
    """
    def __init__(self, role: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        meta = metadata.copy() if metadata else {}
        meta['role'] = role
        super().__init__(role=role, user_id="", task_id="", agent_id="", metadata=meta, content=content)

    @property
    def content(self) -> str:
        return self._content

class SystemMessage(MemoryMessage):
    """
    Represents a system message with role and content.
    All custom attributes are stored in metadata.
    Args:
        role (str): The role of the message sender.
        content (str): The content of the message.
        metadata (Optional[Dict[str, Any]]): Additional metadata.
    """
    def __init__(self, role: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        meta = metadata.copy() if metadata else {}
        meta['role'] = role
        super().__init__(role=role, user_id="", task_id="", agent_id="", metadata=meta, content=content)

    @property
    def content(self) -> str:
        return self._content

class ToolMessage(MemoryMessage):
    """
    Represents a tool message with role and content.
    All custom attributes are stored in metadata.
    Args:
        role (str): The role of the message sender.
        content (str): The content of the message.
        metadata (Optional[Dict[str, Any]]): Additional metadata.
    """
    def __init__(self, role: str, content: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        meta = metadata.copy() if metadata else {}
        meta['role'] = role
        super().__init__(role=role, user_id="", task_id="", agent_id="", metadata=meta, content=content)

    @property
    def content(self) -> str:
        return self._content
