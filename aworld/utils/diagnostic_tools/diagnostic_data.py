import time
from pydantic import BaseModel


class DiagnosticData(BaseModel):
    componentName: str = ""
    info: str = ""
    success: bool = True
    startTime: int = int(time.time() * 1000)
    endTime: int = int(time.time() * 1000)


class AgentResult(BaseModel):
    ...


class ToolResult(BaseModel):
    ...


class RenderData(BaseModel):
    type: str  # agent|action
    tool_name: str
    agent_name: str
    action_name: str
    status: str = ''
    result: list[dict] = [] # Observation extend
