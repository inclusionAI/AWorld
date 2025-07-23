from pydantic import BaseModel


class ContextUsage(BaseModel):
    total_context_length: int = 128000
    used_context_length: int = 0