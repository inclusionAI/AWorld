from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from aworld_gateway.config import WecomChannelConfig
from aworld_gateway.logging import get_gateway_logger
from aworld_gateway.types import InboundEnvelope
from aworld_gateway.channels.wechat.media import (
    build_attachment_prompt,
    build_image_data_url,
    extract_local_file_path,
    sanitize_filename,
)

try:
    import aiohttp
except ImportError:  # pragma: no cover - optional dependency boundary
    aiohttp = None  # type: ignore[assignment]

DEFAULT_WS_URL = "wss://openws.work.weixin.qq.com"

APP_CMD_SUBSCRIBE = "aibot_subscribe"
APP_CMD_CALLBACK = "aibot_msg_callback"
APP_CMD_LEGACY_CALLBACK = "aibot_callback"
APP_CMD_EVENT_CALLBACK = "aibot_event_callback"
APP_CMD_RESPONSE = "aibot_respond_msg"
APP_CMD_SEND = "aibot_send_msg"
APP_CMD_PING = "ping"
APP_CMD_UPLOAD_MEDIA_INIT = "aibot_upload_media_init"
APP_CMD_UPLOAD_MEDIA_CHUNK = "aibot_upload_media_chunk"
APP_CMD_UPLOAD_MEDIA_FINISH = "aibot_upload_media_finish"

CALLBACK_COMMANDS = {APP_CMD_CALLBACK, APP_CMD_LEGACY_CALLBACK}
NON_RESPONSE_COMMANDS = CALLBACK_COMMANDS | {APP_CMD_EVENT_CALLBACK}
DEDUP_MAX_SIZE = 1000
UPLOAD_CHUNK_SIZE = 512 * 1024
IMAGE_MAX_BYTES = 10 * 1024 * 1024
VIDEO_MAX_BYTES = 10 * 1024 * 1024
VOICE_MAX_BYTES = 2 * 1024 * 1024
FILE_MAX_BYTES = 20 * 1024 * 1024
ABSOLUTE_MAX_BYTES = FILE_MAX_BYTES
VOICE_SUPPORTED_MIMES = {"audio/amr"}
CONNECT_TIMEOUT_SECONDS = 20.0
HEARTBEAT_INTERVAL_SECONDS = 30.0
RECONNECT_BACKOFF_SECONDS = [2.0, 5.0, 10.0, 30.0, 60.0]
logger = get_gateway_logger("wecom.connector")


class WecomTransport(Protocol):
    closed: bool

    async def send_json(self, payload: dict[str, object]) -> None: ...
    async def receive_json(self) -> dict[str, object]: ...
    async def close(self) -> None: ...


ConnectFunc = Callable[[str], Awaitable[WecomTransport]]


class _AiohttpTransport:
    def __init__(self, *, session, ws) -> None:
        self._session = session
        self._ws = ws

    @property
    def closed(self) -> bool:
        return bool(getattr(self._ws, "closed", False))

    async def send_json(self, payload: dict[str, object]) -> None:
        await self._ws.send_json(payload)

    async def receive_json(self) -> dict[str, object]:
        while True:
            message = await self._ws.receive()
            if message.type == aiohttp.WSMsgType.TEXT:
                payload = _parse_json(message.data)
                if payload is not None:
                    return payload
                continue
            if message.type in (
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.ERROR,
            ):
                raise RuntimeError("WeCom websocket closed")

    async def close(self) -> None:
        if not self._ws.closed:
            await self._ws.close()
        if not self._session.closed:
            await self._session.close()


async def _default_connect(url: str) -> WecomTransport:
    if aiohttp is None:
        raise RuntimeError("aiohttp is required for WeCom channel.")
    session = aiohttp.ClientSession(trust_env=True)
    ws = await session.ws_connect(url, heartbeat=60, timeout=20)
    return _AiohttpTransport(session=session, ws=ws)


class WecomConnector:
    def __init__(
        self,
        *,
        config: WecomChannelConfig,
        router: object | None = None,
        connect_func: ConnectFunc | None = None,
    ) -> None:
        self.config = config
        self.router = router
        self.started = False
        self._connect_func = connect_func or _default_connect
        self._transport: WecomTransport | None = None
        self._listen_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._pending_responses: dict[str, asyncio.Future[dict[str, object]]] = {}
        self._reply_req_ids: dict[str, str] = {}
        self._last_chat_req_ids: dict[str, str] = {}
        self._seen_message_ids: set[str] = set()
        self._bot_id = ""
        self._secret = ""
        self._ws_url = DEFAULT_WS_URL

    async def start(self) -> None:
        self._bot_id = self._required_env(self.config.bot_id_env, "Missing WeCom bot id env")
        self._secret = self._required_env(self.config.secret_env, "Missing WeCom secret env")
        self._ws_url = self._optional_env(self.config.websocket_url_env) or DEFAULT_WS_URL

        logger.info(
            f"WeCom connector starting bot_id={self._bot_id} ws_url={self._ws_url}"
        )
        self.started = True
        await self._open_connection()
        self._listen_task = asyncio.create_task(self._listen_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info(f"WeCom connector started bot_id={self._bot_id}")

    async def stop(self) -> None:
        logger.info(f"WeCom connector stopping bot_id={self._bot_id}")
        self.started = False
        if self._listen_task is not None:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        self._listen_task = None
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        self._heartbeat_task = None
        for task in list(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()
        self._fail_pending_responses(RuntimeError("WeCom connector stopped"))
        await self._close_transport()
        logger.info(f"WeCom connector stopped bot_id={self._bot_id}")

    async def _open_connection(self) -> None:
        await self._close_transport()
        logger.info(f"WeCom connection opening ws_url={self._ws_url}")
        self._transport = await self._connect_func(self._ws_url)
        req_id = self._new_req_id("subscribe")
        await self._transport.send_json(
            {
                "cmd": APP_CMD_SUBSCRIBE,
                "headers": {"req_id": req_id},
                "body": {
                    "bot_id": self._bot_id,
                    "secret": self._secret,
                },
            }
        )
        response = await self._wait_for_handshake(req_id)
        errcode = response.get("errcode", 0)
        if errcode not in (0, None):
            raise RuntimeError(str(response.get("errmsg") or "WeCom subscribe failed"))
        logger.info(f"WeCom connection opened ws_url={self._ws_url}")

    async def _close_transport(self) -> None:
        if self._transport is not None:
            await self._transport.close()
        self._transport = None

    async def send_text(
        self,
        *,
        chat_id: str,
        text: str,
        reply_to_message_id: str | None = None,
        metadata: dict | None = None,
    ) -> dict[str, object]:
        if not self.started or self._transport is None:
            raise RuntimeError("WeCom connector is not started.")
        if not chat_id:
            raise ValueError("chat_id is required")

        media_requests = self._metadata_media_requests(metadata)
        logger.info(
            "WeCom outbound send requested "
            f"chat_id={chat_id} reply_to={reply_to_message_id} "
            f"text_chars={len(text)} media_count={len(media_requests)}"
        )
        if media_requests:
            return await self._send_with_attachments(
                chat_id=chat_id,
                text=text,
                reply_to_message_id=reply_to_message_id,
                attachments=media_requests,
            )

        reply_req_id = self._reply_req_id_for_message(reply_to_message_id)
        if not reply_req_id:
            reply_req_id = self._last_chat_req_ids.get(chat_id)
        if reply_req_id:
            return await self._send_reply_request(
                reply_req_id,
                {
                    "msgtype": "markdown",
                    "markdown": {"content": text},
                },
            )
        return await self._send_request(
            APP_CMD_SEND,
            {
                "chatid": chat_id,
                "msgtype": "markdown",
                "markdown": {"content": text},
            },
        )

    async def _wait_for_handshake(self, req_id: str) -> dict[str, object]:
        if self._transport is None:
            raise RuntimeError("WeCom transport is not initialized")
        deadline = asyncio.get_running_loop().time() + CONNECT_TIMEOUT_SECONDS
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("Timed out waiting for WeCom subscribe acknowledgement")
            payload = await asyncio.wait_for(self._transport.receive_json(), timeout=remaining)
            cmd = str(payload.get("cmd") or "")
            if cmd in {APP_CMD_PING, APP_CMD_EVENT_CALLBACK}:
                continue
            if self._payload_req_id(payload) == req_id:
                return payload

    async def _listen_loop(self) -> None:
        backoff_index = 0
        while self.started:
            transport = self._transport
            if transport is None:
                delay = RECONNECT_BACKOFF_SECONDS[min(backoff_index, len(RECONNECT_BACKOFF_SECONDS) - 1)]
                backoff_index += 1
                await asyncio.sleep(delay)
                try:
                    await self._open_connection()
                    backoff_index = 0
                    continue
                except Exception as exc:
                    logger.warning(f"WeCom reconnect failed ws_url={self._ws_url} error={exc}")
                    continue
            try:
                while self.started and self._transport is transport and not transport.closed:
                    payload = await transport.receive_json()
                    await self._dispatch_payload(payload)
                if not self.started:
                    return
                raise RuntimeError("WeCom websocket closed")
            except asyncio.CancelledError:
                return
            except Exception:
                if not self.started:
                    return
                self._fail_pending_responses(RuntimeError("WeCom connection interrupted"))
                logger.warning("WeCom connection interrupted, scheduling reconnect")
                delay = RECONNECT_BACKOFF_SECONDS[min(backoff_index, len(RECONNECT_BACKOFF_SECONDS) - 1)]
                backoff_index += 1
                await asyncio.sleep(delay)
                try:
                    await self._open_connection()
                    backoff_index = 0
                except Exception as exc:
                    logger.warning(f"WeCom reconnect failed ws_url={self._ws_url} error={exc}")
                    continue

    async def _heartbeat_loop(self) -> None:
        while self.started:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                return
            transport = self._transport
            if transport is None or transport.closed:
                continue
            try:
                await transport.send_json(
                    {
                        "cmd": APP_CMD_PING,
                        "headers": {"req_id": self._new_req_id("ping")},
                        "body": {},
                    }
                )
            except Exception:
                continue

    async def _dispatch_payload(self, payload: dict[str, object]) -> None:
        req_id = self._payload_req_id(payload)
        cmd = str(payload.get("cmd") or "")

        if req_id and req_id in self._pending_responses and cmd not in NON_RESPONSE_COMMANDS:
            future = self._pending_responses.get(req_id)
            if future is not None and not future.done():
                future.set_result(payload)
            return

        if cmd in CALLBACK_COMMANDS:
            self._schedule_callback(payload)
            return
        logger.info(f"WeCom payload ignored cmd={cmd or 'unknown'}")

    def _schedule_callback(self, payload: dict[str, object]) -> None:
        task = asyncio.create_task(self._process_callback_with_logging(payload))
        self._background_tasks.add(task)
        task.add_done_callback(self._finalize_background_task)

    def _finalize_background_task(self, task: asyncio.Task[None]) -> None:
        self._background_tasks.discard(task)

    async def _process_callback_with_logging(self, payload: dict[str, object]) -> None:
        try:
            await self._process_callback(payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("WeCom callback processing failed")

    async def _process_callback(self, payload: dict[str, object]) -> None:
        body = payload.get("body")
        if not isinstance(body, dict):
            logger.info("WeCom callback skipped reason=missing_body")
            return

        message_id = str(body.get("msgid") or self._payload_req_id(payload) or uuid.uuid4().hex).strip()
        if not message_id:
            logger.info("WeCom callback skipped reason=missing_message_id")
            return
        if message_id in self._seen_message_ids:
            logger.info(f"WeCom callback skipped reason=duplicate message_id={message_id}")
            return
        self._seen_message_ids.add(message_id)
        self._trim_seen_message_ids()

        sender = body.get("from") if isinstance(body.get("from"), dict) else {}
        sender_id = str(sender.get("userid") or "").strip()
        chat_id = str(body.get("chatid") or sender_id).strip()
        if not chat_id:
            logger.info(
                f"WeCom callback skipped reason=missing_chat_id message_id={message_id}"
            )
            return

        conversation_type = "group" if str(body.get("chattype") or "").lower() == "group" else "dm"
        if conversation_type == "group":
            if not self._is_group_allowed(chat_id):
                logger.info(
                    f"WeCom callback skipped reason=group_policy message_id={message_id} conversation={chat_id}"
                )
                return
        elif not self._is_dm_allowed(sender_id):
            logger.info(
                f"WeCom callback skipped reason=dm_policy message_id={message_id} sender={sender_id}"
            )
            return

        req_id = self._payload_req_id(payload)
        self._remember_reply_req_id(message_id, req_id)
        self._remember_chat_req_id(chat_id, req_id)

        text = self._extract_text(body)
        inbound_media = await self._extract_inbound_media(body, message_id=message_id)
        attachments = inbound_media["attachments"]
        attachment_prompt = build_attachment_prompt(attachments)
        if attachment_prompt:
            text = f"{text}\n\n{attachment_prompt}".strip() if text else attachment_prompt
        if not text:
            logger.info(
                f"WeCom callback skipped reason=empty_text message_id={message_id} conversation={chat_id}"
            )
            return
        if self.router is None:
            logger.info(
                f"WeCom callback skipped reason=no_router message_id={message_id} conversation={chat_id}"
            )
            return

        logger.info(
            "WeCom inbound message "
            f"conversation={chat_id} sender={sender_id} message_id={message_id} "
            f"attachments={len(attachments)}"
        )

        metadata: dict[str, Any] = {}
        if attachments:
            metadata["attachments"] = attachments
            metadata["wecom_media"] = inbound_media["wecom_media"]
            metadata["multimodal_parts"] = inbound_media["multimodal_parts"]

        outbound = await self.router.handle_inbound(
            InboundEnvelope(
                channel="wecom",
                account_id=self._bot_id,
                conversation_id=chat_id,
                conversation_type=conversation_type,
                sender_id=sender_id,
                sender_name=sender_id or None,
                message_id=message_id,
                text=text,
                raw_payload=payload,
                metadata=metadata,
            ),
            channel_default_agent_id=self.config.default_agent_id,
        )
        logger.info(
            "WeCom outbound reply "
            f"conversation={outbound.conversation_id} reply_to={outbound.reply_to_message_id} "
            f"chars={len(outbound.text)}"
        )
        await self.send_text(
            chat_id=outbound.conversation_id,
            text=outbound.text,
            reply_to_message_id=outbound.reply_to_message_id,
            metadata=outbound.metadata,
        )

    async def _send_request(
        self,
        cmd: str,
        body: dict[str, object],
        timeout: float = 15.0,
    ) -> dict[str, object]:
        if self._transport is None or self._transport.closed:
            raise RuntimeError("WeCom transport is not connected")
        req_id = self._new_req_id(cmd)
        future: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
        self._pending_responses[req_id] = future
        try:
            logger.info(f"WeCom request sending cmd={cmd} req_id={req_id}")
            await self._transport.send_json({"cmd": cmd, "headers": {"req_id": req_id}, "body": body})
            response = await asyncio.wait_for(future, timeout=timeout)
            logger.info(f"WeCom request completed cmd={cmd} req_id={req_id}")
            return response
        finally:
            self._pending_responses.pop(req_id, None)

    async def _send_reply_request(
        self,
        reply_req_id: str,
        body: dict[str, object],
        timeout: float = 15.0,
    ) -> dict[str, object]:
        if self._transport is None or self._transport.closed:
            raise RuntimeError("WeCom transport is not connected")
        normalized_req_id = str(reply_req_id or "").strip()
        if not normalized_req_id:
            raise ValueError("reply_req_id is required")
        future: asyncio.Future[dict[str, object]] = asyncio.get_running_loop().create_future()
        self._pending_responses[normalized_req_id] = future
        try:
            logger.info(
                f"WeCom reply request sending req_id={normalized_req_id} msgtype={body.get('msgtype')}"
            )
            await self._transport.send_json(
                {"cmd": APP_CMD_RESPONSE, "headers": {"req_id": normalized_req_id}, "body": body}
            )
            response = await asyncio.wait_for(future, timeout=timeout)
            logger.info(
                f"WeCom reply request completed req_id={normalized_req_id} msgtype={body.get('msgtype')}"
            )
            return response
        finally:
            self._pending_responses.pop(normalized_req_id, None)

    def _remember_reply_req_id(self, message_id: str, req_id: str) -> None:
        normalized_message_id = str(message_id or "").strip()
        normalized_req_id = str(req_id or "").strip()
        if not normalized_message_id or not normalized_req_id:
            return
        self._reply_req_ids[normalized_message_id] = normalized_req_id
        while len(self._reply_req_ids) > DEDUP_MAX_SIZE:
            self._reply_req_ids.pop(next(iter(self._reply_req_ids)))

    def _remember_chat_req_id(self, chat_id: str, req_id: str) -> None:
        normalized_chat_id = str(chat_id or "").strip()
        normalized_req_id = str(req_id or "").strip()
        if not normalized_chat_id or not normalized_req_id:
            return
        self._last_chat_req_ids[normalized_chat_id] = normalized_req_id
        while len(self._last_chat_req_ids) > DEDUP_MAX_SIZE:
            self._last_chat_req_ids.pop(next(iter(self._last_chat_req_ids)))

    def _reply_req_id_for_message(self, reply_to: str | None) -> str | None:
        normalized = str(reply_to or "").strip()
        if not normalized:
            return None
        return self._reply_req_ids.get(normalized)

    def _trim_seen_message_ids(self) -> None:
        while len(self._seen_message_ids) > DEDUP_MAX_SIZE:
            self._seen_message_ids.pop()

    async def _extract_inbound_media(
        self,
        body: dict[str, object],
        *,
        message_id: str,
    ) -> dict[str, list[dict[str, Any]]]:
        attachments: list[dict[str, Any]] = []
        wecom_media: list[dict[str, Any]] = []
        multimodal_parts: list[dict[str, Any]] = []
        refs: list[tuple[str, dict[str, Any]]] = []

        msgtype = str(body.get("msgtype") or "").lower()
        if isinstance(body.get("image"), dict):
            refs.append(("image", body["image"]))
        if msgtype == "file" and isinstance(body.get("file"), dict):
            refs.append(("file", body["file"]))
        if msgtype == "appmsg" and isinstance(body.get("appmsg"), dict):
            appmsg = body["appmsg"]
            if isinstance(appmsg.get("file"), dict):
                refs.append(("file", appmsg["file"]))
            elif isinstance(appmsg.get("image"), dict):
                refs.append(("image", appmsg["image"]))

        for index, (kind, ref) in enumerate(refs):
            cached = await self._cache_media(kind, ref, message_id=message_id, item_index=index)
            if cached is None:
                continue
            attachments.append(cached["attachment"])
            wecom_media.append(cached["wecom_media"])
            if cached["multimodal_part"] is not None:
                multimodal_parts.append(cached["multimodal_part"])

        return {
            "attachments": attachments,
            "wecom_media": wecom_media,
            "multimodal_parts": multimodal_parts,
        }

    async def _send_with_attachments(
        self,
        *,
        chat_id: str,
        text: str,
        reply_to_message_id: str | None,
        attachments: list[dict[str, object]],
    ) -> dict[str, object]:
        reply_req_id = self._reply_req_id_for_message(reply_to_message_id)
        if not reply_req_id:
            reply_req_id = self._last_chat_req_ids.get(chat_id)

        media_result: dict[str, object] | None = None
        for attachment in attachments:
            raw_path = str(attachment.get("path") or "").strip()
            attachment_type = str(attachment.get("type") or "").strip().lower()
            file_name = None
            local_path = extract_local_file_path(raw_path)
            if local_path is not None:
                file_name = local_path.name
            try:
                prepared = await self._prepare_outbound_media(
                    raw_path,
                    attachment_type=attachment_type,
                    force_file_attachment=bool(attachment.get("force_file_attachment")),
                    file_name=file_name,
                )
            except Exception as exc:
                logger.exception(
                    "WeCom outbound media preparation failed "
                    f"chat_id={chat_id} attachment_type={attachment_type} path={raw_path} error={exc}"
                )
                return {"error": str(exc)}
            if prepared["rejected"]:
                logger.warning(
                    "WeCom outbound media rejected "
                    f"chat_id={chat_id} attachment_type={attachment_type} "
                    f"reason={prepared['reject_reason']}"
                )
                note_result = await self._send_followup_markdown(
                    chat_id=chat_id,
                    text=str(prepared["reject_reason"] or ""),
                    reply_req_id=reply_req_id,
                )
                return {"error": str(prepared["reject_reason"] or ""), "caption": note_result}
            if not prepared["data"]:
                continue
            if prepared["downgraded"]:
                logger.info(
                    "WeCom outbound media downgraded "
                    f"chat_id={chat_id} original_type={prepared['detected_type']} "
                    f"final_type={prepared['final_type']} reason={prepared['downgrade_note']}"
                )
            upload = await self._upload_media_bytes(
                prepared["data"],
                str(prepared["final_type"]),
                str(prepared["file_name"]),
            )
            if reply_req_id:
                media_result = await self._send_reply_media_message(
                    reply_req_id,
                    str(prepared["final_type"]),
                    str(upload.get("media_id") or ""),
                )
            else:
                media_result = await self._send_media_message(
                    chat_id,
                    str(prepared["final_type"]),
                    str(upload.get("media_id") or ""),
                )
            if media_result is not None:
                media_result = {
                    "upload": upload,
                    "body": {
                        "type": prepared["final_type"],
                        "media_id": upload.get("media_id"),
                    },
                    "response": media_result,
                }
            if prepared["downgraded"] and prepared["downgrade_note"]:
                await self._send_followup_markdown(
                    chat_id=chat_id,
                    text=str(prepared["downgrade_note"]),
                    reply_req_id=reply_req_id,
                )

        caption_result: dict[str, object] | None = None
        if text.strip():
            caption_result = await self._send_followup_markdown(
                chat_id=chat_id,
                text=text,
                reply_req_id=reply_req_id,
            )

        result: dict[str, object] = {}
        if media_result is not None:
            upload_payload = media_result.get("upload")
            response_payload = media_result.get("response")
            media_body = media_result.get("body")
            result["media"] = {
                "body": media_body if isinstance(media_body, dict) else {},
                "upload": upload_payload if isinstance(upload_payload, dict) else {},
                "response": response_payload if isinstance(response_payload, dict) else {},
            }
        if caption_result is not None:
            result["caption"] = caption_result
        return result

    async def _send_followup_markdown(
        self,
        *,
        chat_id: str,
        text: str,
        reply_req_id: str | None,
    ) -> dict[str, object]:
        if reply_req_id:
            return await self._send_reply_request(
                reply_req_id,
                {
                    "msgtype": "markdown",
                    "markdown": {"content": text},
                },
            )
        return await self._send_request(
            APP_CMD_SEND,
            {
                "chatid": chat_id,
                "msgtype": "markdown",
                "markdown": {"content": text},
            },
        )

    async def _prepare_outbound_media(
        self,
        media_source: str,
        *,
        attachment_type: str,
        force_file_attachment: bool,
        file_name: str | None = None,
    ) -> dict[str, object]:
        data, content_type, resolved_name = await self._load_outbound_media(
            media_source,
            file_name=file_name,
        )
        detected_type = self._detect_outbound_media_type_from_content(
            content_type=content_type,
            attachment_type=attachment_type,
            force_file_attachment=force_file_attachment,
        )
        size_check = self._apply_file_size_limits(
            file_size=len(data),
            detected_type=detected_type,
            content_type=content_type,
        )
        return {
            "data": data,
            "content_type": content_type,
            "file_name": resolved_name,
            "detected_type": detected_type,
            **size_check,
        }

    async def _load_outbound_media(
        self,
        media_source: str,
        *,
        file_name: str | None = None,
    ) -> tuple[bytes, str, str]:
        local_path = extract_local_file_path(media_source)
        if local_path is None:
            raise FileNotFoundError(f"Media file not found: {media_source}")
        data = await asyncio.to_thread(local_path.read_bytes)
        resolved_name = file_name or local_path.name
        content_type = mimetypes.guess_type(resolved_name)[0] or "application/octet-stream"
        return data, content_type, resolved_name

    async def _upload_media_bytes(
        self,
        data: bytes,
        media_type: str,
        filename: str,
    ) -> dict[str, object]:
        if not data:
            raise ValueError("Cannot upload empty media")

        total_size = len(data)
        total_chunks = (total_size + UPLOAD_CHUNK_SIZE - 1) // UPLOAD_CHUNK_SIZE
        init_response = await self._send_request(
            APP_CMD_UPLOAD_MEDIA_INIT,
            {
                "type": media_type,
                "filename": filename,
                "total_size": total_size,
                "total_chunks": total_chunks,
                "md5": hashlib.md5(data).hexdigest(),
            },
        )
        init_body = init_response.get("body") if isinstance(init_response.get("body"), dict) else {}
        upload_id = str(init_body.get("upload_id") or "").strip()
        if not upload_id:
            raise RuntimeError(f"media upload init failed: missing upload_id in response {init_response}")

        for chunk_index, start in enumerate(range(0, total_size, UPLOAD_CHUNK_SIZE)):
            chunk = data[start : start + UPLOAD_CHUNK_SIZE]
            await self._send_request(
                APP_CMD_UPLOAD_MEDIA_CHUNK,
                {
                    "upload_id": upload_id,
                    "chunk_index": chunk_index,
                    "base64_data": base64.b64encode(chunk).decode("ascii"),
                },
            )

        finish_response = await self._send_request(
            APP_CMD_UPLOAD_MEDIA_FINISH,
            {"upload_id": upload_id},
        )
        finish_body = finish_response.get("body") if isinstance(finish_response.get("body"), dict) else {}
        media_id = str(finish_body.get("media_id") or "").strip()
        if not media_id:
            raise RuntimeError(f"media upload finish failed: missing media_id in response {finish_response}")
        return finish_body

    async def _send_media_message(
        self,
        chat_id: str,
        media_type: str,
        media_id: str,
    ) -> dict[str, object]:
        return await self._send_request(
            APP_CMD_SEND,
            {
                "chatid": chat_id,
                "msgtype": media_type,
                media_type: {"media_id": media_id},
            },
        )

    async def _send_reply_media_message(
        self,
        reply_req_id: str,
        media_type: str,
        media_id: str,
    ) -> dict[str, object]:
        return await self._send_reply_request(
            reply_req_id,
            {
                "msgtype": media_type,
                media_type: {"media_id": media_id},
            },
        )

    def _metadata_media_requests(self, metadata: dict | None) -> list[dict[str, object]]:
        if not isinstance(metadata, dict):
            return []
        raw_items = metadata.get("outbound_attachments")
        if not isinstance(raw_items, list):
            return []
        requests: list[dict[str, object]] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            raw_path = str(
                raw_item.get("path")
                or raw_item.get("file_path")
                or raw_item.get("local_path")
                or ""
            ).strip()
            if not raw_path:
                continue
            requests.append(
                {
                    "path": raw_path,
                    "type": str(raw_item.get("type") or "").strip().lower(),
                    "force_file_attachment": bool(raw_item.get("force_file_attachment")),
                }
            )
        return requests

    @staticmethod
    def _detect_outbound_media_type(
        *,
        local_path: Path,
        attachment_type: str,
        force_file_attachment: bool,
    ) -> str:
        if force_file_attachment or attachment_type == "file":
            return "file"
        if attachment_type in {"image", "video", "voice"}:
            return attachment_type
        mime_type = mimetypes.guess_type(str(local_path))[0] or "application/octet-stream"
        if mime_type.startswith("image/"):
            return "image"
        if mime_type.startswith("video/"):
            return "video"
        if mime_type.startswith("audio/"):
            return "voice"
        return "file"

    @staticmethod
    def _detect_outbound_media_type_from_content(
        *,
        content_type: str,
        attachment_type: str,
        force_file_attachment: bool,
    ) -> str:
        if force_file_attachment or attachment_type == "file":
            return "file"
        if attachment_type in {"image", "video", "voice"}:
            return attachment_type
        mime_type = str(content_type or "").strip().lower()
        if mime_type.startswith("image/"):
            return "image"
        if mime_type.startswith("video/"):
            return "video"
        if mime_type.startswith("audio/") or mime_type == "application/ogg":
            return "voice"
        return "file"

    @staticmethod
    def _apply_file_size_limits(
        *,
        file_size: int,
        detected_type: str,
        content_type: str,
    ) -> dict[str, object]:
        file_size_mb = file_size / (1024 * 1024)
        normalized_type = str(detected_type or "file").lower()
        normalized_content_type = str(content_type or "").strip().lower()

        if file_size > ABSOLUTE_MAX_BYTES:
            return {
                "final_type": normalized_type,
                "rejected": True,
                "reject_reason": (
                    f"文件大小 {file_size_mb:.2f}MB 超过了企业微信允许的最大限制 20MB，无法发送。"
                ),
                "downgraded": False,
                "downgrade_note": None,
            }
        if normalized_type == "image" and file_size > IMAGE_MAX_BYTES:
            return {
                "final_type": "file",
                "rejected": False,
                "reject_reason": None,
                "downgraded": True,
                "downgrade_note": f"图片大小 {file_size_mb:.2f}MB 超过 10MB 限制，已转为文件格式发送",
            }
        if normalized_type == "video" and file_size > VIDEO_MAX_BYTES:
            return {
                "final_type": "file",
                "rejected": False,
                "reject_reason": None,
                "downgraded": True,
                "downgrade_note": f"视频大小 {file_size_mb:.2f}MB 超过 10MB 限制，已转为文件格式发送",
            }
        if normalized_type == "voice":
            if normalized_content_type and normalized_content_type not in VOICE_SUPPORTED_MIMES:
                return {
                    "final_type": "file",
                    "rejected": False,
                    "reject_reason": None,
                    "downgraded": True,
                    "downgrade_note": f"语音格式 {normalized_content_type} 不支持，企微仅支持 AMR 格式，已转为文件格式发送",
                }
            if file_size > VOICE_MAX_BYTES:
                return {
                    "final_type": "file",
                    "rejected": False,
                    "reject_reason": None,
                    "downgraded": True,
                    "downgrade_note": f"语音大小 {file_size_mb:.2f}MB 超过 2MB 限制，已转为文件格式发送",
                }
        return {
            "final_type": normalized_type,
            "rejected": False,
            "reject_reason": None,
            "downgraded": False,
            "downgrade_note": None,
        }

    async def _cache_media(
        self,
        kind: str,
        media: dict[str, Any],
        *,
        message_id: str,
        item_index: int,
    ) -> dict[str, Any] | None:
        payload_b64 = str(media.get("base64") or "").strip()
        if not payload_b64:
            return None
        try:
            payload = base64.b64decode(payload_b64)
        except Exception:
            return None
        if not payload:
            return None

        if kind == "image":
            ext = self._detect_image_ext(payload)
            mime_type = mimetypes.types_map.get(ext, "image/jpeg")
            file_name = str(media.get("filename") or media.get("name") or f"image{ext}").strip() or f"image{ext}"
        else:
            file_name = str(media.get("filename") or media.get("name") or "wecom_file").strip() or "wecom_file"
            mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"

        path = await asyncio.to_thread(
            self._write_inbound_attachment,
            message_id,
            item_index,
            file_name,
            payload,
        )
        attachment = {
            "type": kind,
            "path": str(path),
            "file_name": file_name,
            "mime_type": mime_type,
        }
        media_entry = {
            "kind": kind,
            "local_path": str(path),
            "file_name": file_name,
            "mime_type": mime_type,
            "size_bytes": len(payload),
            "item_index": item_index,
        }
        data_url = build_image_data_url(payload, mime_type)
        multimodal_part = {"type": "image_url", "image_url": {"url": data_url}} if data_url else None
        return {
            "attachment": attachment,
            "wecom_media": media_entry,
            "multimodal_part": multimodal_part,
        }

    def _write_inbound_attachment(
        self,
        message_id: str,
        item_index: int,
        file_name: str,
        payload: bytes,
    ) -> Path:
        target_dir = (Path.cwd() / ".aworld" / "gateway" / "wecom" / "attachments" / sanitize_filename(self._bot_id)).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_message_id = sanitize_filename(message_id).replace(".", "_")
        safe_name = sanitize_filename(file_name)
        target_path = target_dir / f"{safe_message_id}_{item_index}_{safe_name}"
        target_path.write_bytes(payload)
        return target_path

    @staticmethod
    def _detect_image_ext(data: bytes) -> str:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"
        if data.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        if data.startswith((b"GIF87a", b"GIF89a")):
            return ".gif"
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return ".webp"
        return ".jpg"

    def _is_dm_allowed(self, sender_id: str) -> bool:
        if self.config.dm_policy == "disabled":
            return False
        if self.config.dm_policy == "allowlist":
            return sender_id in self.config.allow_from
        return True

    def _is_group_allowed(self, group_id: str) -> bool:
        if self.config.group_policy == "disabled":
            return False
        if self.config.group_policy == "allowlist":
            return group_id in self.config.group_allow_from
        return True

    @staticmethod
    def _extract_text(body: dict[str, object]) -> str:
        msgtype = str(body.get("msgtype") or "").lower()
        text_parts: list[str] = []
        if msgtype == "mixed":
            mixed = body.get("mixed") if isinstance(body.get("mixed"), dict) else {}
            items = mixed.get("msg_item") if isinstance(mixed.get("msg_item"), list) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                if str(item.get("msgtype") or "").lower() != "text":
                    continue
                text_block = item.get("text") if isinstance(item.get("text"), dict) else {}
                content = str(text_block.get("content") or "").strip()
                if content:
                    text_parts.append(content)
            return "\n".join(text_parts).strip()

        text_block = body.get("text") if isinstance(body.get("text"), dict) else {}
        content = str(text_block.get("content") or "").strip()
        if content:
            text_parts.append(content)
        if msgtype == "voice":
            voice_block = body.get("voice") if isinstance(body.get("voice"), dict) else {}
            voice_text = str(voice_block.get("content") or "").strip()
            if voice_text:
                text_parts.append(voice_text)
        return "\n".join(text_parts).strip()

    @staticmethod
    def _payload_req_id(payload: dict[str, object]) -> str:
        headers = payload.get("headers")
        if isinstance(headers, dict):
            return str(headers.get("req_id") or "")
        return ""

    @staticmethod
    def _new_req_id(prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex}"

    @staticmethod
    def _optional_env(name: str | None) -> str:
        if not name:
            return ""
        return str(os.getenv(name, "")).strip()

    def _required_env(self, name: str | None, error_message: str) -> str:
        value = self._optional_env(name)
        if value:
            return value
        raise ValueError(error_message)

    def _fail_pending_responses(self, exc: Exception) -> None:
        for req_id, future in list(self._pending_responses.items()):
            if not future.done():
                future.set_exception(exc)
            self._pending_responses.pop(req_id, None)


def _parse_json(raw: Any) -> dict[str, object] | None:
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None
