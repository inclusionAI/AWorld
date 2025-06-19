import uuid
from abc import abstractmethod
from typing import Any, AsyncGenerator, List, Optional
from pydantic import BaseModel, Field


class ChatCompletionMessage(BaseModel):
    role: str = Field(..., description="The role of the message")
    content: str = Field(..., description="The content of the message")


class ChatCompletionRequest(BaseModel):
    user_id: str = Field(None, description="The user id")
    session_id: str = Field(
        None,
        description="The session id, if not provided, a new session will be created",
    )
    query_id: str = Field(None, description="The query id")
    model: str = Field(..., description="The model to use")
    messages: List[ChatCompletionMessage] = Field(
        ..., description="The messages to send to the agent"
    )


class ChatCompletionChoice(BaseModel):
    index: int = 0
    delta: ChatCompletionMessage = Field(
        ..., description="The delta message from the agent"
    )


class ChatCompletionResponse(BaseModel):
    object: str = "chat.completion.chunk"
    id: str = uuid.uuid4().hex
    choices: List[ChatCompletionChoice] = Field(
        ..., description="The choices from the agent"
    )


class AgentModel(BaseModel):
    id: str = Field(..., description="The agent id")
    name: Optional[str] = Field(None, description="The agent name")
    description: Optional[str] = Field(None, description="The agent description")
    path: str = Field(..., description="The agent path")
    instance: Any = Field(..., description="The agent module instance", exclude=True)


class BaseAWorldAgent:
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def description(self) -> str:
        pass

    @abstractmethod
    async def run(
        self, prompt: str = None, request: ChatCompletionRequest = None
    ) -> AsyncGenerator[str, None]:
        pass
