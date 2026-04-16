from __future__ import annotations

from dataclasses import dataclass

NEW_SESSION_COMMANDS = {"/new", "/summary", "新会话", "压缩上下文"}


@dataclass(frozen=True)
class DingdingBridgeResult:
    text: str


@dataclass(frozen=True)
class IncomingAttachment:
    download_code: str
    file_name: str


@dataclass(frozen=True)
class ExtractedMessage:
    text: str
    attachments: list[IncomingAttachment]
