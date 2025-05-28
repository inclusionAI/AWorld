from typing import Optional, Any

from pydantic import BaseModel, Field


class AworldTask(BaseModel):
    task_id: str = Field(default=None, description="task id")
    agent_id: str = Field(default=None, description="agent id")
    agent_input: str = Field(default=None, description="agent input")
    session_id: Optional[str] = Field(default=None, description="session id")
    user_id: Optional[str] = Field(default=None, description="user id")
    llm_provider: Optional[str] = Field(default=None, description="llm provider")
    llm_model_name: Optional[str] = Field(default=None, description="llm model name")
    llm_api_key: Optional[str] = Field(default=None, description="llm api key")
    llm_base_url: Optional[str] = Field(default=None, description="llm base url")
    task_system_prompt: Optional[str] = Field(default=None, description="task_system_prompt")
    mcp_servers: Optional[list[str]] = Field(default=None, description="mcp_servers")
    node_id: Optional[str] = Field(default=None, description="execute task node_id")
    client_id: Optional[str] = Field(default=None, description="submit client ip")

class AworldTaskResult(BaseModel):
    task: AworldTask = Field(default=None, description="task")
    server_host: str = Field(default=None, description="aworld server id")
    data: Any = Field(default=None, description="result data")

