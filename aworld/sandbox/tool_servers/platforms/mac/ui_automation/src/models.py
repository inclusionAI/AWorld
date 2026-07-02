from dataclasses import dataclass
from typing import Optional


@dataclass
class SeeTarget:
    target_id: str
    role: Optional[str] = None
    text: Optional[str] = None
    x: Optional[int] = None
    y: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None


@dataclass
class ClickRequest:
    target_id: Optional[str] = None
    x: Optional[int] = None
    y: Optional[int] = None
    app: Optional[str] = None
    window_id: Optional[str] = None
    window_title: Optional[str] = None
    timeout_seconds: Optional[float] = None
