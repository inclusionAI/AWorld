from typing import List
import uuid
from pydantic import BaseModel, Field


class AgentModel(BaseModel):
    agent_id: str = Field(..., description="The agent id")
    agent_name: str = Field(..., description="The agent name")
    agent_description: str = Field(..., description="The agent description")
    agent_type: str = Field(..., description="The agent type")
    agent_status: str = Field(..., description="The agent status")


class ChatCompletionMessage(BaseModel):
    role: str = Field(..., description="The role of the message")
    content: str = Field(..., description="The content of the message")


class ChatCompletionRequest(BaseModel):
    user_id: str = Field(..., description="The user id")
    session_id: str = Field(
        ...,
        description="The session id, if not provided, a new session will be created",
    )
    query_id: str = Field(..., description="The query id")
    model: str = Field(..., description="The model to use")
    messages: List[ChatCompletionMessage] = Field(
        ..., description="The messages to send to the agent"
    )
    prompt: str = Field(..., description="The prompt to send to the agent")


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
