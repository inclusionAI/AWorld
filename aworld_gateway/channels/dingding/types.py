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


@dataclass
class AICardInstance:
    card_instance_id: str
    access_token: str
    inputing_started: bool = False


@dataclass(frozen=True)
class PendingFileMessage:
    media_id: str
    file_name: str
    file_type: str


@dataclass(frozen=True)
class ExtractedMessage:
    text: str
    attachments: list[IncomingAttachment]
