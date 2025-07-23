from pydantic import BaseModel, Field





class TraceState(BaseModel):
    trace_id: str = Field(default=None, description="")
