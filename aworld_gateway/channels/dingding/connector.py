from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import re
import time
from pathlib import Path
from urllib.parse import unquote
from uuid import uuid4

import httpx

from aworld_gateway.channels.dingding.bridge import AworldDingdingBridge
from aworld_gateway.channels.dingding.types import (
    AICardInstance,
    ExtractedMessage,
    IncomingAttachment,
    NEW_SESSION_COMMANDS,
    PendingFileMessage,
)
from aworld_gateway.config import DingdingChannelConfig

DINGTALK_API = "https://api.dingtalk.com"
OAPI_API = "https://oapi.dingtalk.com"
MEDIA_MAX_BYTES = 20 * 1024 * 1024
AI_CARD_REQUEST_RETRIES = 2
AI_CARD_RETRY_DELAY_SECONDS = 0.3
MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")
LOCAL_PATH_RE = re.compile(r"^(?:/|~|[A-Za-z]:[\\/])")


class DingTalkConnector:
    def __init__(
        self,
        *,
        config: DingdingChannelConfig,
        bridge: AworldDingdingBridge,
        stream_module,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._bridge = bridge
        self._stream_module = stream_module
        self._http = http_client or httpx.AsyncClient(timeout=60.0)
        self._session_ids: dict[str, str] = {}
        self._client = None
        self._access_token: str | None = None
        self._access_token_expiry: float = 0.0
        self._oapi_access_token: str | None = None
        self._oapi_access_token_expiry: float = 0.0

    async def start(self) -> None:
        credential = self._stream_module.Credential(
            self._required_env(self._config.client_id_env),
            self._required_env(self._config.client_secret_env),
        )
        self._client = self._stream_module.DingTalkStreamClient(credential)
        connector = self

        class _MessageHandler(self._stream_module.ChatbotHandler):
            async def process(self, callback):
                payload = getattr(callback, "data", callback)
                await connector.handle_callback(payload)
                status_ok = getattr(
                    connector._stream_module.AckMessage,
                    "STATUS_OK",
                    "ok",
                )
                return status_ok, "OK"

        self._client.register_callback_handler(
            self._stream_module.ChatbotMessage.TOPIC,
            _MessageHandler(),
        )

    async def stop(self) -> None:
        await self._http.aclose()

    async def handle_callback(self, callback_payload) -> None:
        data = self._parse_data(callback_payload)

        session_webhook = str(data.get("sessionWebhook") or "").strip()
        if not session_webhook:
            return

        sender_id = str(data.get("senderStaffId") or data.get("senderId") or "").strip()
        if not sender_id:
            return

        message = self._extract_message(data)
        user_text = message.text.strip()
        if not user_text and not message.attachments:
            return

        conversation_key = str(data.get("conversationId") or sender_id).strip()
        if user_text.lower() in {command.lower() for command in NEW_SESSION_COMMANDS}:
            self._session_ids[conversation_key] = self._new_session_id(conversation_key)
            await self.send_text(
                session_webhook=session_webhook,
                text="✨ 已开启新会话，之前的上下文已清空。",
            )
            return

        session_id = self._session_ids.get(conversation_key)
        if not session_id:
            session_id = self._new_session_id(conversation_key)
            self._session_ids[conversation_key] = session_id

        await self._run_message_round(
            session_webhook=session_webhook,
            session_id=session_id,
            text=message.text,
            data=data,
        )

    async def send_text(self, *, session_webhook: str, text: str) -> None:
        token = await self._get_access_token()
        response = await self._http.post(
            session_webhook,
            headers={
                "x-acs-dingtalk-access-token": token,
                "Content-Type": "application/json",
            },
            json={"msgtype": "text", "text": {"content": text}},
        )
        response.raise_for_status()

    async def _run_message_round(
        self,
        *,
        session_webhook: str,
        session_id: str,
        text: str,
        data: dict,
    ) -> None:
        active_card = (
            await self._try_create_ai_card(data)
            if self._config.enable_ai_card
            else None
        )
        streamed_parts: list[str] = []

        async def on_text_chunk(chunk: str) -> None:
            if not chunk:
                return
            streamed_parts.append(chunk)
            if active_card is not None:
                await self._stream_ai_card(
                    active_card,
                    "".join(streamed_parts),
                    finished=False,
                )

        try:
            result = await self._bridge.run(
                agent_id=self._config.default_agent_id or "aworld",
                session_id=session_id,
                text=text,
                on_text_chunk=on_text_chunk,
            )
        except Exception as exc:
            await self._send_error_to_client(
                session_webhook=session_webhook,
                card=active_card,
                text=f"抱歉，调用 Agent 失败：{exc}",
            )
            return

        final_text, pending_files = await self._process_local_media_links(result.text)
        display_text = final_text or ("✅ 媒体已发送" if pending_files else "（空响应）")

        if active_card is not None and await self._finish_ai_card(active_card, display_text):
            await self._send_pending_files(session_webhook, pending_files)
            return

        await self.send_text(session_webhook=session_webhook, text=display_text)
        await self._send_pending_files(session_webhook, pending_files)

    @staticmethod
    def _parse_data(raw) -> dict:
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return {}

    @staticmethod
    def _new_session_id(conversation_key: str) -> str:
        return f"dingtalk_{conversation_key}_{uuid4().hex[:8]}"

    @staticmethod
    def _extract_message(data: dict) -> ExtractedMessage:
        msg_type = str(data.get("msgtype") or "text")
        content = data.get("content", {}) or {}

        if msg_type == "text":
            text_data = data.get("text")
            if isinstance(text_data, dict):
                text = str(text_data.get("content") or "")
                if bool(text_data.get("isReplyMsg")):
                    text = f"[回复消息]\n{text}" if text else "[回复消息]"
            else:
                text = str(data.get("content") or "")
            return ExtractedMessage(text=text, attachments=[])

        if msg_type == "richText":
            parts = content.get("richText", []) if isinstance(content, dict) else []
            text_parts: list[str] = []
            attachments: list[IncomingAttachment] = []
            for part in parts:
                if not isinstance(part, dict):
                    continue
                text = str(part.get("text") or "")
                if text:
                    text_parts.append(text)
                download_code = DingTalkConnector._extract_download_code(part)
                if download_code:
                    attachments.append(
                        IncomingAttachment(
                            download_code=download_code,
                            file_name=str(part.get("fileName") or ""),
                        )
                    )
            return ExtractedMessage(text="".join(text_parts), attachments=attachments)

        if msg_type == "audio":
            if isinstance(content, dict):
                return ExtractedMessage(
                    text=str(content.get("recognition") or "[语音消息]"),
                    attachments=[],
                )
            return ExtractedMessage(text="[语音消息]", attachments=[])

        if msg_type == "picture":
            attachment = DingTalkConnector._attachment_from_content(content, file_name="")
            return ExtractedMessage(
                text="[图片]",
                attachments=[attachment] if attachment is not None else [],
            )

        if msg_type == "file":
            raw_name = str(content.get("fileName") or "") if isinstance(content, dict) else ""
            display_name = raw_name or "文件"
            attachment = DingTalkConnector._attachment_from_content(content, file_name=raw_name)
            return ExtractedMessage(
                text=f"[文件:{display_name}]",
                attachments=[attachment] if attachment is not None else [],
            )

        text_data = data.get("text")
        if isinstance(text_data, dict):
            text = str(text_data.get("content") or "")
        else:
            text = str(data.get("content") or "")
        if text:
            return ExtractedMessage(text=text, attachments=[])
        return ExtractedMessage(text=f"[{msg_type}消息]", attachments=[])

    @staticmethod
    def _required_env(name: str | None) -> str:
        key = (name or "").strip()
        value = os.getenv(key, "").strip()
        if not value:
            raise ValueError(f"Missing required env var: {name}")
        return value

    async def _get_access_token(self) -> str:
        now = time.time()
        if self._access_token and self._access_token_expiry - 60 > now:
            return self._access_token

        response = await self._http.post(
            f"{DINGTALK_API}/v1.0/oauth2/accessToken",
            json={
                "appKey": self._required_env(self._config.client_id_env),
                "appSecret": self._required_env(self._config.client_secret_env),
            },
        )
        response.raise_for_status()
        data = response.json()
        token = str(data["accessToken"])
        self._access_token = token
        self._access_token_expiry = now + int(data.get("expireIn", 7200))
        return token

    async def _get_oapi_access_token(self) -> str | None:
        now = time.time()
        if self._oapi_access_token and self._oapi_access_token_expiry - 60 > now:
            return self._oapi_access_token

        try:
            response = await self._http.get(
                f"{OAPI_API}/gettoken",
                params={
                    "appkey": self._required_env(self._config.client_id_env),
                    "appsecret": self._required_env(self._config.client_secret_env),
                },
            )
            response.raise_for_status()
            data = response.json()
            if int(data.get("errcode", 1)) != 0:
                return None
            token = str(data.get("access_token") or "").strip()
            if not token:
                return None
            self._oapi_access_token = token
            self._oapi_access_token_expiry = now + int(data.get("expires_in", 7200))
            return token
        except Exception:
            return None

    async def _process_local_media_links(
        self,
        content: str,
    ) -> tuple[str, list[PendingFileMessage]]:
        if not self._config.enable_attachments or not content:
            return content, []
        if (
            "attachment://" not in content
            and "file://" not in content
            and "MEDIA:" not in content
        ):
            return content, []

        oapi_token = await self._get_oapi_access_token()
        if not oapi_token:
            return content, []

        result = content
        pending_files: list[PendingFileMessage] = []

        for match in list(MARKDOWN_IMAGE_RE.finditer(result)):
            full_match, alt_text, raw_url = match.group(0), match.group(1), match.group(2)
            local_path = self._extract_local_file_path(raw_url)
            if not local_path or not self._is_image_path(local_path):
                continue
            media_id = await self._upload_local_file_to_dingtalk(
                local_path,
                "image",
                oapi_token,
            )
            if not media_id:
                continue
            result = result.replace(full_match, f"![{alt_text}]({media_id})", 1)

        for match in list(MARKDOWN_LINK_RE.finditer(result)):
            full_match, _link_text, raw_url = match.group(0), match.group(1), match.group(2)
            local_path = self._extract_local_file_path(raw_url)
            if not local_path:
                continue
            media_id = await self._upload_local_file_to_dingtalk(
                local_path,
                "file",
                oapi_token,
            )
            if not media_id:
                continue
            pending_files.append(
                PendingFileMessage(
                    media_id=media_id,
                    file_name=local_path.name,
                    file_type=local_path.suffix.lstrip(".").lower() or "bin",
                )
            )
            result = result.replace(full_match, "", 1)

        return self._cleanup_processed_text(result), pending_files

    async def _upload_local_file_to_dingtalk(
        self,
        local_path: Path,
        media_type: str,
        oapi_token: str,
    ) -> str | None:
        try:
            file_stat = local_path.stat()
            if file_stat.st_size <= 0 or file_stat.st_size > MEDIA_MAX_BYTES:
                return None
            payload = local_path.read_bytes()
            mime_type = mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"
            response = await self._http.post(
                f"{OAPI_API}/media/upload",
                params={"access_token": oapi_token, "type": media_type},
                files={"media": (local_path.name, payload, mime_type)},
            )
            response.raise_for_status()
            media_id = str(response.json().get("media_id") or "").strip()
            return media_id or None
        except Exception:
            return None

    async def _send_pending_files(
        self,
        session_webhook: str,
        pending_files: list[PendingFileMessage],
    ) -> None:
        if not pending_files:
            return
        token = await self._get_access_token()
        headers = {
            "x-acs-dingtalk-access-token": token,
            "Content-Type": "application/json",
        }
        for item in pending_files:
            try:
                response = await self._http.post(
                    session_webhook,
                    headers=headers,
                    json={
                        "msgtype": "file",
                        "file": {
                            "mediaId": item.media_id,
                            "fileName": item.file_name,
                            "fileType": item.file_type,
                        },
                    },
                )
                response.raise_for_status()
            except Exception:
                continue

    async def _try_create_ai_card(self, data: dict) -> AICardInstance | None:
        if not self._config.enable_ai_card:
            return None
        card_template_env = str(self._config.card_template_id_env or "").strip()
        card_template_id = os.getenv(card_template_env, "").strip()
        if not card_template_id:
            return None

        target = self._build_card_target(data)
        if target is None:
            return None

        try:
            token = await self._get_access_token()
            card_instance_id = f"card_{int(time.time() * 1000)}_{uuid4().hex[:8]}"
            headers = {
                "x-acs-dingtalk-access-token": token,
                "Content-Type": "application/json",
            }
            create_resp = await self._request_with_retry(
                "POST",
                f"{DINGTALK_API}/v1.0/card/instances",
                headers=headers,
                json={
                    "cardTemplateId": card_template_id,
                    "outTrackId": card_instance_id,
                    "cardData": {"cardParamMap": {}},
                    "callbackType": "STREAM",
                    "imGroupOpenSpaceModel": {"supportForward": True},
                    "imRobotOpenSpaceModel": {"supportForward": True},
                },
            )
            create_resp.raise_for_status()
            deliver_resp = await self._request_with_retry(
                "POST",
                f"{DINGTALK_API}/v1.0/card/instances/deliver",
                headers=headers,
                json={"outTrackId": card_instance_id, "userIdType": 1, **target},
            )
            deliver_resp.raise_for_status()
            return AICardInstance(
                card_instance_id=card_instance_id,
                access_token=token,
            )
        except Exception:
            return None

    async def _stream_ai_card(
        self,
        card: AICardInstance,
        content: str,
        finished: bool,
    ) -> bool:
        headers = {
            "x-acs-dingtalk-access-token": card.access_token,
            "Content-Type": "application/json",
        }
        try:
            if not card.inputing_started:
                status_resp = await self._request_with_retry(
                    "PUT",
                    f"{DINGTALK_API}/v1.0/card/instances",
                    headers=headers,
                    json={
                        "outTrackId": card.card_instance_id,
                        "cardData": {
                            "cardParamMap": {
                                "flowStatus": "2",
                                "msgContent": "",
                                "staticMsgContent": "",
                                "sys_full_json_obj": json.dumps(
                                    {"order": ["msgContent"]},
                                    ensure_ascii=False,
                                ),
                            }
                        },
                    },
                )
                status_resp.raise_for_status()
                card.inputing_started = True

            stream_resp = await self._request_with_retry(
                "PUT",
                f"{DINGTALK_API}/v1.0/card/streaming",
                headers=headers,
                json={
                    "outTrackId": card.card_instance_id,
                    "guid": f"{int(time.time() * 1000)}_{uuid4().hex[:6]}",
                    "key": "msgContent",
                    "content": content,
                    "isFull": True,
                    "isFinalize": finished,
                    "isError": False,
                },
            )
            stream_resp.raise_for_status()
            return True
        except Exception:
            return False

    async def _finish_ai_card(self, card: AICardInstance, content: str) -> bool:
        headers = {
            "x-acs-dingtalk-access-token": card.access_token,
            "Content-Type": "application/json",
        }
        try:
            if not await self._stream_ai_card(card, content, finished=True):
                return False
            response = await self._request_with_retry(
                "PUT",
                f"{DINGTALK_API}/v1.0/card/instances",
                headers=headers,
                json={
                    "outTrackId": card.card_instance_id,
                    "cardData": {
                        "cardParamMap": {
                            "flowStatus": "3",
                            "msgContent": content,
                            "staticMsgContent": "",
                            "sys_full_json_obj": json.dumps(
                                {"order": ["msgContent"]},
                                ensure_ascii=False,
                            ),
                        }
                    },
                },
            )
            response.raise_for_status()
            return True
        except Exception:
            return False

    async def _send_error_to_client(
        self,
        *,
        session_webhook: str,
        card: AICardInstance | None,
        text: str,
    ) -> None:
        if card is not None and await self._finish_ai_card(card, text):
            return
        await self.send_text(session_webhook=session_webhook, text=text)

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json: dict,
    ):
        last_error: Exception | None = None
        for attempt in range(1, AI_CARD_REQUEST_RETRIES + 1):
            try:
                return await self._http.request(
                    method,
                    url,
                    headers=headers,
                    json=json,
                )
            except Exception as exc:
                last_error = exc
                if attempt < AI_CARD_REQUEST_RETRIES:
                    await asyncio.sleep(AI_CARD_RETRY_DELAY_SECONDS)
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Failed request without exception: {method} {url}")

    def _build_card_target(self, data: dict) -> dict | None:
        if str(data.get("conversationType") or "") == "2":
            conversation_id = str(data.get("conversationId") or "").strip()
            robot_code = str(data.get("robotCode") or "").strip()
            if not conversation_id:
                return None
            if not robot_code:
                robot_code = self._required_env(self._config.client_id_env)
            return {
                "openSpaceId": f"dtv1.card//IM_GROUP.{conversation_id}",
                "imGroupOpenDeliverModel": {"robotCode": robot_code},
            }

        user_id = str(data.get("senderStaffId") or data.get("senderId") or "").strip()
        if not user_id:
            return None
        return {
            "openSpaceId": f"dtv1.card//IM_ROBOT.{user_id}",
            "imRobotOpenDeliverModel": {"spaceType": "IM_ROBOT"},
        }

    @staticmethod
    def _extract_download_code(content: dict) -> str:
        return str(
            content.get("downloadCode") or content.get("pictureDownloadCode") or ""
        ).strip()

    @staticmethod
    def _attachment_from_content(content, *, file_name: str) -> IncomingAttachment | None:
        if not isinstance(content, dict):
            return None
        download_code = DingTalkConnector._extract_download_code(content)
        if not download_code:
            return None
        return IncomingAttachment(download_code=download_code, file_name=file_name)

    @staticmethod
    def _extract_local_file_path(raw_url: str) -> Path | None:
        candidate = raw_url.strip().strip("<>").strip("'").strip('"')
        if not candidate:
            return None
        candidate = candidate.replace("\\ ", " ")
        if candidate.startswith("file://"):
            candidate = candidate[len("file://") :]
        elif candidate.startswith("MEDIA:"):
            candidate = candidate[len("MEDIA:") :]
        elif candidate.startswith("attachment://"):
            candidate = candidate[len("attachment://") :]
        candidate = unquote(candidate).strip()
        if not candidate or not LOCAL_PATH_RE.match(candidate):
            return None
        path = Path(candidate).expanduser()
        if not path.is_absolute() or not path.exists() or not path.is_file():
            return None
        return path

    @staticmethod
    def _is_image_path(local_path: Path) -> bool:
        mime_type, _ = mimetypes.guess_type(str(local_path))
        if mime_type and mime_type.startswith("image/"):
            return True
        return local_path.suffix.lower() in {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".bmp",
            ".webp",
            ".tif",
            ".tiff",
            ".svg",
        }

    @staticmethod
    def _cleanup_processed_text(content: str) -> str:
        compact = re.sub(r"[ \t]+\n", "\n", content)
        compact = re.sub(r"\n{3,}", "\n\n", compact)
        return compact.strip()
