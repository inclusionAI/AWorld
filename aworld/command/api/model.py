from typing import List
from pydantic import BaseModel, Field

class AgentModel(BaseModel):
    agent_id: str = Field(..., description="The agent id")
    agent_name: str = Field(..., description="The agent name")
    agent_description: str = Field(..., description="The agent description")
    agent_type: str = Field(..., description="The agent type")
    agent_status: str = Field(..., description="The agent status")


class ChatRequest(BaseModel):
    user_id: str = Field(..., description="The user id")
    agent_id: str = Field(..., description="The agent id")
    session_id: str = Field(..., description="The session id, if not provided, a new session will be created")
    query_id: str = Field(..., description="The query id")
    prompt: str = Field(..., description="The prompt to send to the agent")

class ChatCompletionMessage(BaseModel):
    role: str = Field(..., description="The role of the message")
    content: str = Field(..., description="The content of the message")

class ChatCompletionChoice(BaseModel):
    message: ChatCompletionMessage = Field(..., description="The message from the agent")

class ChatCompletion(BaseModel):
    choices: List[ChatCompletionChoice] = Field(..., description="The choices from the agent")

class ChatResponse(BaseModel):
    completion: ChatCompletion = Field(..., description="The completion from the agent")
