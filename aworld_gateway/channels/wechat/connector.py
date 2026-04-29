from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from aworld_gateway.channels.wechat.account_store import load_account
from aworld_gateway.channels.wechat.context_token_store import ContextTokenStore
from aworld_gateway.channels.wechat.media import (
    DEFAULT_CDN_BASE_URL,
    ITEM_FILE,
    ITEM_IMAGE,
    ITEM_TEXT,
    ITEM_VIDEO,
    ITEM_VOICE,
    OutboundMediaRequest,
    aes128_ecb_decrypt,
    aes128_ecb_encrypt,
    aes_padded_size,
    assert_wechat_cdn_url,
    build_attachment_prompt,
    build_image_data_url,
    build_outbound_media_item,
    cdn_download_url,
    cdn_upload_url,
    extract_local_file_path,
    extract_outbound_media_requests,
    mime_from_filename,
    parse_aes_key,
    sanitize_filename,
)
from aworld_gateway.config import WechatChannelConfig
from aworld_gateway.cron_push import CronPushBindingStore, CronPushBridge
from aworld_gateway.logging import get_gateway_logger
from aworld_gateway.types import InboundEnvelope

try:
    import aiohttp
except ImportError:  # pragma: no cover - optional dependency boundary
    aiohttp = None  # type: ignore[assignment]

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
CHANNEL_VERSION = "2.2.0"
ILINK_APP_ID = "bot"
ILINK_APP_CLIENT_VERSION = str((2 << 16) | (2 << 8) | 0)
EP_GET_UPDATES = "ilink/bot/getupdates"
EP_SEND_MESSAGE = "ilink/bot/sendmessage"
EP_GET_UPLOAD_URL = "ilink/bot/getuploadurl"
DEDUP_MAX_SIZE = 1000
LOG_TEXT_TRUNCATE_LIMIT = 300

SendMessageFunc = Callable[..., Awaitable[dict[str, object]]]
GetUpdatesFunc = Callable[..., Awaitable[dict[str, object]]]
DownloadMediaFunc = Callable[..., Awaitable[bytes]]
GetUploadUrlFunc = Callable[..., Awaitable[dict[str, object]]]
UploadCiphertextFunc = Callable[..., Awaitable[str]]
SendMediaMessageFunc = Callable[..., Awaitable[dict[str, object]]]
logger = get_gateway_logger("wechat.connector")


def _base_info() -> dict[str, str]:
    return {"channel_version": CHANNEL_VERSION}


async def _default_post_json(
    *,
    session,
    base_url: str,
    token: str,
    endpoint: str,
    payload: dict[str, Any],
    timeout_ms: int,
) -> dict[str, object]:
    body = _json_dumps({**payload, "base_info": _base_info()})
    async with session.post(
        _endpoint_url(base_url, endpoint),
        data=body,
        headers=_headers(token, body),
        timeout=_build_timeout(timeout_ms),
    ) as response:
        raw = await response.text()
        if not getattr(response, "ok", False):
            raise RuntimeError(f"iLink POST {endpoint} HTTP {response.status}: {raw[:200]}")
        return json.loads(raw)


async def _default_send_message(
    *,
    session,
    base_url: str,
    token: str,
    to: str,
    text: str,
    context_token: str | None,
    client_id: str,
) -> dict[str, object]:
    payload: dict[str, Any] = {
        "msg": {
            "from_user_id": "",
            "to_user_id": to,
            "client_id": client_id,
            "message_type": 2,
            "message_state": 2,
            "item_list": [{"type": ITEM_TEXT, "text_item": {"text": text}}],
        }
    }
    if context_token:
        payload["msg"]["context_token"] = context_token
    return await _default_post_json(
        session=session,
        base_url=base_url,
        token=token,
        endpoint=EP_SEND_MESSAGE,
        payload=payload,
        timeout_ms=15_000,
    )


async def _default_send_media_message(
    *,
    session,
    base_url: str,
    token: str,
    to: str,
    item: dict[str, object],
    context_token: str | None,
    client_id: str,
) -> dict[str, object]:
    payload: dict[str, Any] = {
        "msg": {
            "from_user_id": "",
            "to_user_id": to,
            "client_id": client_id,
            "message_type": 2,
            "message_state": 2,
            "item_list": [item],
        }
    }
    if context_token:
        payload["msg"]["context_token"] = context_token
    return await _default_post_json(
        session=session,
        base_url=base_url,
        token=token,
        endpoint=EP_SEND_MESSAGE,
        payload=payload,
        timeout_ms=15_000,
    )


async def _default_get_updates(
    *,
    session,
    base_url: str,
    token: str,
    sync_buf: str,
    timeout_ms: int,
) -> dict[str, object]:
    try:
        return await _default_post_json(
            session=session,
            base_url=base_url,
            token=token,
            endpoint=EP_GET_UPDATES,
            payload={"get_updates_buf": sync_buf},
            timeout_ms=timeout_ms,
        )
    except asyncio.TimeoutError:
        return {"ret": 0, "msgs": [], "get_updates_buf": sync_buf}


async def _default_get_upload_url(
    *,
    session,
    base_url: str,
    token: str,
    to_user_id: str,
    media_type: int,
    filekey: str,
    rawsize: int,
    rawfilemd5: str,
    filesize: int,
    aeskey_hex: str,
) -> dict[str, object]:
    return await _default_post_json(
        session=session,
        base_url=base_url,
        token=token,
        endpoint=EP_GET_UPLOAD_URL,
        payload={
            "filekey": filekey,
            "media_type": media_type,
            "to_user_id": to_user_id,
            "rawsize": rawsize,
            "rawfilemd5": rawfilemd5,
            "filesize": filesize,
            "no_need_thumb": True,
            "aeskey": aeskey_hex,
        },
        timeout_ms=15_000,
    )


async def _default_upload_ciphertext(
    *,
    session,
    ciphertext: bytes,
    upload_url: str,
) -> str:
    async with session.post(
        upload_url,
        data=ciphertext,
        headers={"Content-Type": "application/octet-stream"},
        timeout=_build_timeout_seconds(120.0),
    ) as response:
        if response.status == 200:
            encrypted_param = response.headers.get("x-encrypted-param")
            if encrypted_param:
                await response.read()
                return encrypted_param
            raw = await response.text()
            raise RuntimeError(f"CDN upload missing x-encrypted-param header: {raw[:200]}")
        raw = await response.text()
        raise RuntimeError(f"CDN upload HTTP {response.status}: {raw[:200]}")


async def _download_bytes(
    *,
    session,
    url: str,
    timeout_seconds: float,
) -> bytes:
    async with session.get(url, timeout=_build_timeout_seconds(timeout_seconds)) as response:
        response.raise_for_status()
        return await response.read()


async def _default_download_media(
    *,
    session,
    cdn_base_url: str,
    encrypted_query_param: str | None,
    aes_key_b64: str | None,
    full_url: str | None,
    timeout_seconds: float,
) -> bytes:
    if encrypted_query_param:
        raw = await _download_bytes(
            session=session,
            url=cdn_download_url(cdn_base_url, encrypted_query_param),
            timeout_seconds=timeout_seconds,
        )
    elif full_url:
        assert_wechat_cdn_url(full_url)
        raw = await _download_bytes(
            session=session,
            url=full_url,
            timeout_seconds=timeout_seconds,
        )
    else:
        raise RuntimeError("media item had neither encrypt_query_param nor full_url")
    if aes_key_b64:
        raw = aes128_ecb_decrypt(raw, parse_aes_key(aes_key_b64))
    return raw


class WechatConnector:
    def __init__(
        self,
        *,
        config: WechatChannelConfig,
        router: object | None = None,
        storage_root: Path | None = None,
        get_updates_func: GetUpdatesFunc | None = None,
        send_message_func: SendMessageFunc | None = None,
        download_media_func: DownloadMediaFunc | None = None,
        get_upload_url_func: GetUploadUrlFunc | None = None,
        upload_ciphertext_func: UploadCiphertextFunc | None = None,
        send_media_message_func: SendMediaMessageFunc | None = None,
    ) -> None:
        self.config = config
        self.router = router
        self.started = False
        self._storage_root = storage_root or (Path.cwd() / ".aworld" / "gateway" / "wechat")
        self._token_store = ContextTokenStore(self._storage_root)
        self._get_updates_func = get_updates_func or _default_get_updates
        self._send_message_func = send_message_func or _default_send_message
        self._download_media_func = download_media_func or _default_download_media
        self._get_upload_url_func = get_upload_url_func or _default_get_upload_url
        self._upload_ciphertext_func = upload_ciphertext_func or _default_upload_ciphertext
        self._send_media_message_func = send_media_message_func or _default_send_media_message
        self._poll_session: object | None = None
        self._send_session: object | None = None
        self._poll_task: asyncio.Task[None] | None = None
        self._account_id = ""
        self._token = ""
        self._base_url = DEFAULT_BASE_URL
        self._cdn_base_url = DEFAULT_CDN_BASE_URL
        self._cron_scheduler = None
        self._sync_buf = ""
        self._seen_message_ids: dict[str, None] = {}
        self._cron_push_bridge = CronPushBridge(
            binding_store=CronPushBindingStore(self._storage_root / "cron-push-bindings.json")
        )
        self._cron_push_bridge.register_sender("wechat", self._send_cron_push_text)

    async def start(self) -> None:
        from aworld.core.scheduler import get_scheduler

        account_id_env = self._optional_env(self.config.account_id_env)
        token_env = self._optional_env(self.config.token_env)
        base_url_env = self._optional_env(self.config.base_url_env)
        cdn_base_url_env = self._optional_env(self.config.cdn_base_url_env)

        persisted = load_account(self._storage_root, account_id_env) if account_id_env else None
        self._account_id = account_id_env or str((persisted or {}).get("account_id") or "")
        self._token = token_env or str((persisted or {}).get("token") or "")
        self._base_url = base_url_env or str((persisted or {}).get("base_url") or "") or DEFAULT_BASE_URL
        self._cdn_base_url = cdn_base_url_env or DEFAULT_CDN_BASE_URL

        if not self._account_id:
            raise ValueError("Missing WeChat account id env")
        if not self._token:
            raise ValueError("Missing WeChat token env")

        logger.info(
            "WeChat connector starting "
            f"account={self._account_id} base_url={self._base_url} "
            f"storage_root={self._storage_root.resolve()}"
        )
        self._token_store.restore(self._account_id)
        self._poll_session = self._build_session()
        self._send_session = self._build_session()
        self._cron_scheduler = get_scheduler()
        self._cron_push_bridge.install_scheduler_sink(self._cron_scheduler)
        self.started = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info(
            "WeChat connector started "
            f"account={self._account_id} poll_task={self._poll_task is not None}"
        )

    async def stop(self) -> None:
        logger.info(f"WeChat connector stopping account={self._account_id}")
        self.started = False
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        self._poll_task = None
        await _close_session(self._poll_session)
        await _close_session(self._send_session)
        self._poll_session = None
        self._send_session = None
        if self._cron_scheduler is not None:
            self._cron_push_bridge.uninstall_scheduler_sink(self._cron_scheduler)
            self._cron_scheduler = None
        logger.info(f"WeChat connector stopped account={self._account_id}")

    async def send_text(
        self,
        *,
        chat_id: str,
        text: str,
        metadata: dict | None = None,
    ) -> dict[str, object]:
        if not self.started or self._send_session is None:
            raise RuntimeError("WeChat connector is not started.")

        context_token = self._token_store.get(self._account_id, chat_id)
        cleaned_text, inline_requests = extract_outbound_media_requests(text)
        metadata_requests = self._metadata_media_requests(metadata)
        media_requests = [*inline_requests, *metadata_requests]
        last_result: dict[str, object] = {}
        logger.info(
            "WeChat outbound send requested "
            f"chat_id={chat_id} text_chars={len(cleaned_text or text)} "
            f"media_count={len(media_requests)} has_context_token={bool(context_token)}"
        )

        if cleaned_text:
            last_result = await self._send_text_chunks(
                chat_id=chat_id,
                text=cleaned_text,
                context_token=context_token,
            )
        elif not media_requests:
            last_result = await self._send_text_chunks(
                chat_id=chat_id,
                text=text,
                context_token=context_token,
            )

        for request in media_requests:
            last_result = await self._send_local_media(
                chat_id=chat_id,
                request=request,
                context_token=context_token,
            )
        return last_result

    async def _send_text_chunks(
        self,
        *,
        chat_id: str,
        text: str,
        context_token: str | None,
    ) -> dict[str, object]:
        last_result: dict[str, object] = {}
        for chunk in self._split_text(text):
            client_id = f"aworld-wechat-{uuid.uuid4().hex}"
            try:
                result = await self._send_message_func(
                    session=self._send_session,
                    base_url=self._base_url,
                    token=self._token,
                    to=chat_id,
                    text=chunk,
                    context_token=context_token,
                    client_id=client_id,
                )
            except Exception:
                logger.exception(
                    "WeChat outbound text chunk failed "
                    f"chat_id={chat_id} client_id={client_id} chars={len(chunk)}"
                )
                raise
            if "client_id" not in result:
                result["client_id"] = client_id
            last_result = result
            logger.info(
                "WeChat outbound text chunk sent "
                f"chat_id={chat_id} client_id={client_id} chars={len(chunk)}"
            )
        return last_result

    async def _send_local_media(
        self,
        *,
        chat_id: str,
        request: OutboundMediaRequest,
        context_token: str | None,
    ) -> dict[str, object]:
        logger.info(
            "WeChat outbound media upload starting "
            f"chat_id={chat_id} path={request.path} "
            f"kind={request.media_kind_override or 'auto'}"
        )
        try:
            plaintext = await asyncio.to_thread(request.path.read_bytes)
            rawsize = len(plaintext)
            rawfilemd5 = hashlib.md5(plaintext).hexdigest()
            filekey = secrets.token_hex(16)
            aes_key = secrets.token_bytes(16)
            media_type, media_item = build_outbound_media_item(
                path=request.path,
                encrypted_query_param="",
                aes_key_for_api="",
                ciphertext_size=0,
                plaintext_size=rawsize,
                rawfilemd5=rawfilemd5,
                force_file_attachment=request.force_file_attachment,
                media_kind_override=request.media_kind_override,
            )
            upload_response = await self._get_upload_url_func(
                session=self._send_session,
                base_url=self._base_url,
                token=self._token,
                to_user_id=chat_id,
                media_type=media_type,
                filekey=filekey,
                rawsize=rawsize,
                rawfilemd5=rawfilemd5,
                filesize=aes_padded_size(rawsize),
                aeskey_hex=aes_key.hex(),
            )
            upload_param = str(upload_response.get("upload_param") or "").strip()
            upload_full_url = str(upload_response.get("upload_full_url") or "").strip()
            ciphertext = aes128_ecb_encrypt(plaintext, aes_key)
            if upload_full_url:
                upload_url = upload_full_url
            elif upload_param:
                upload_url = cdn_upload_url(self._cdn_base_url, upload_param, filekey)
            else:
                raise RuntimeError(
                    f"getUploadUrl returned neither upload_param nor upload_full_url: {upload_response}"
                )
            encrypted_query_param = await self._upload_ciphertext_func(
                session=self._send_session,
                ciphertext=ciphertext,
                upload_url=upload_url,
            )
            aes_key_for_api = base64.b64encode(aes_key.hex().encode("ascii")).decode("ascii")
            _media_type, media_item = build_outbound_media_item(
                path=request.path,
                encrypted_query_param=encrypted_query_param,
                aes_key_for_api=aes_key_for_api,
                ciphertext_size=len(ciphertext),
                plaintext_size=rawsize,
                rawfilemd5=rawfilemd5,
                force_file_attachment=request.force_file_attachment,
                media_kind_override=request.media_kind_override,
            )
            client_id = f"aworld-wechat-{uuid.uuid4().hex}"
            result = await self._send_media_message_func(
                session=self._send_session,
                base_url=self._base_url,
                token=self._token,
                to=chat_id,
                item=media_item,
                context_token=context_token,
                client_id=client_id,
            )
            if "client_id" not in result:
                result["client_id"] = client_id
            logger.info(
                "WeChat outbound media sent "
                f"chat_id={chat_id} client_id={client_id} bytes={rawsize} path={request.path}"
            )
            return result
        except Exception:
            logger.exception(
                "WeChat outbound media failed "
                f"chat_id={chat_id} path={request.path} "
                f"kind={request.media_kind_override or 'auto'}"
            )
            raise

    async def _process_message(self, message: dict[str, Any]) -> None:
        sender_id = str(message.get("from_user_id") or "").strip()
        if not sender_id:
            return

        message_id = str(message.get("message_id") or "").strip()
        if self._remember_seen_message_id(message_id):
            return

        context_token = str(message.get("context_token") or "").strip()
        if context_token:
            self._token_store.set(self._account_id, sender_id, context_token)

        conversation_type, conversation_id = self._conversation_target(message, sender_id)
        if conversation_type == "group":
            if not self._is_group_allowed(conversation_id):
                logger.info(
                    "WeChat inbound message skipped "
                    f"conversation={conversation_id} message_id={message_id} reason=group_policy"
                )
                return
        elif not self._is_dm_allowed(sender_id):
            logger.info(
                "WeChat inbound message skipped "
                f"conversation={conversation_id} message_id={message_id} reason=dm_policy"
            )
            return

        item_list = message.get("item_list") or []
        text = self._extract_text(item_list)
        inbound_media = await self._collect_inbound_attachments(
            message_id=message_id or f"wx-{uuid.uuid4().hex}",
            item_list=item_list,
        )
        attachments = inbound_media["attachments"]
        attachment_prompt = build_attachment_prompt(attachments)
        if attachment_prompt:
            text = f"{text}\n\n{attachment_prompt}".strip() if text else attachment_prompt
        if not text:
            logger.info(
                "WeChat inbound message skipped "
                f"conversation={conversation_id} message_id={message_id} reason=empty_text"
            )
            return

        if self.router is None:
            logger.info(
                "WeChat inbound message dropped "
                f"conversation={conversation_id} message_id={message_id} reason=no_router"
            )
            return

        logger.info(
            "WeChat inbound message "
            f"conversation={conversation_id} sender={sender_id} message_id={message_id} "
            f"attachments={len(attachments)} text={self._truncate_log_text(text, limit=LOG_TEXT_TRUNCATE_LIMIT)}"
        )

        metadata: dict[str, Any] = {}
        if attachments:
            metadata["attachments"] = attachments
            metadata["wechat_media"] = inbound_media["wechat_media"]
            metadata["multimodal_parts"] = inbound_media["multimodal_parts"]

        async def on_output(output) -> None:
            self._cron_push_bridge.bind_output(
                output,
                {
                    "channel": "wechat",
                    "account_id": self._account_id,
                    "conversation_id": conversation_id,
                    "sender_id": sender_id,
                    "target": {"chat_id": conversation_id},
                },
            )

        outbound = await self.router.handle_inbound(
            InboundEnvelope(
                channel="wechat",
                account_id=self._account_id,
                conversation_id=conversation_id,
                conversation_type=conversation_type,
                sender_id=sender_id,
                sender_name=sender_id,
                message_id=message_id or f"wx-{uuid.uuid4().hex}",
                text=text,
                raw_payload=message,
                metadata=metadata,
            ),
            channel_default_agent_id=self.config.default_agent_id,
            on_output=on_output,
        )
        logger.info(
            "WeChat outbound reply "
            f"chat_id={outbound.conversation_id} reply_to={outbound.reply_to_message_id or message_id} "
            f"text={self._truncate_log_text(outbound.text, limit=LOG_TEXT_TRUNCATE_LIMIT)}"
        )
        await self.send_text(
            chat_id=outbound.conversation_id,
            text=outbound.text,
            metadata=outbound.metadata,
        )

    async def _send_cron_push_text(self, binding, text: str, notification) -> None:
        target = binding.get("target") if isinstance(binding, dict) else None
        if not isinstance(target, dict):
            return
        chat_id = str(target.get("chat_id") or "").strip()
        if not chat_id:
            return
        await self.send_text(chat_id=chat_id, text=text)

    async def _collect_inbound_attachments(
        self,
        *,
        message_id: str,
        item_list: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        if self._poll_session is None:
            return {"attachments": [], "wechat_media": [], "multimodal_parts": []}
        attachments: list[dict[str, str]] = []
        wechat_media: list[dict[str, Any]] = []
        multimodal_parts: list[dict[str, Any]] = []
        for index, item in enumerate(item_list):
            candidate = self._inbound_media_candidate(item)
            if candidate is None:
                continue
            try:
                payload = await self._download_media_func(
                    session=self._poll_session,
                    cdn_base_url=self._cdn_base_url,
                    encrypted_query_param=candidate["encrypted_query_param"],
                    aes_key_b64=candidate["aes_key_b64"],
                    full_url=candidate["full_url"],
                    timeout_seconds=candidate["timeout_seconds"],
                )
            except Exception:
                logger.exception(
                    "WeChat inbound media download failed "
                    f"message_id={message_id} index={index}"
                )
                continue
            path = await asyncio.to_thread(
                self._write_inbound_attachment,
                message_id,
                index,
                candidate["file_name"],
                payload,
            )
            attachments.append(
                {
                    "type": candidate["type"],
                    "path": str(path),
                    "file_name": candidate["file_name"],
                    "mime_type": candidate["mime_type"],
                }
            )
            wechat_media.append(
                {
                    "kind": candidate["type"],
                    "local_path": str(path),
                    "file_name": candidate["file_name"],
                    "mime_type": candidate["mime_type"],
                    "size_bytes": len(payload),
                    "item_index": index,
                }
            )
            image_data_url = build_image_data_url(payload, candidate["mime_type"])
            if image_data_url:
                multimodal_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": image_data_url},
                    }
                )
        return {
            "attachments": attachments,
            "wechat_media": wechat_media,
            "multimodal_parts": multimodal_parts,
        }

    def _write_inbound_attachment(
        self,
        message_id: str,
        index: int,
        file_name: str,
        payload: bytes,
    ) -> Path:
        target_dir = (self._storage_root / "attachments" / sanitize_filename(self._account_id)).resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_message_id = sanitize_filename(message_id).replace(".", "_")
        safe_name = sanitize_filename(file_name)
        target_path = target_dir / f"{safe_message_id}_{index}_{safe_name}"
        target_path.write_bytes(payload)
        return target_path

    def _inbound_media_candidate(self, item: dict[str, Any]) -> dict[str, Any] | None:
        item_type = item.get("type")
        if item_type == ITEM_IMAGE:
            image_item = item.get("image_item") or {}
            media = image_item.get("media") or {}
            return {
                "type": "image",
                "file_name": "image.jpg",
                "mime_type": "image/jpeg",
                "encrypted_query_param": media.get("encrypt_query_param"),
                "aes_key_b64": self._image_aes_key(image_item),
                "full_url": media.get("full_url"),
                "timeout_seconds": 30.0,
            }
        if item_type == ITEM_VIDEO:
            video_item = item.get("video_item") or {}
            media = video_item.get("media") or {}
            return {
                "type": "video",
                "file_name": "video.mp4",
                "mime_type": "video/mp4",
                "encrypted_query_param": media.get("encrypt_query_param"),
                "aes_key_b64": media.get("aes_key"),
                "full_url": media.get("full_url"),
                "timeout_seconds": 120.0,
            }
        if item_type == ITEM_FILE:
            file_item = item.get("file_item") or {}
            media = file_item.get("media") or {}
            file_name = str(file_item.get("file_name") or "document.bin")
            return {
                "type": "file",
                "file_name": file_name,
                "mime_type": mime_from_filename(file_name),
                "encrypted_query_param": media.get("encrypt_query_param"),
                "aes_key_b64": media.get("aes_key"),
                "full_url": media.get("full_url"),
                "timeout_seconds": 60.0,
            }
        if item_type == ITEM_VOICE:
            voice_item = item.get("voice_item") or {}
            media = voice_item.get("media") or {}
            return {
                "type": "voice",
                "file_name": "voice.silk",
                "mime_type": "audio/silk",
                "encrypted_query_param": media.get("encrypt_query_param"),
                "aes_key_b64": media.get("aes_key"),
                "full_url": media.get("full_url"),
                "timeout_seconds": 60.0,
            }
        return None

    @staticmethod
    def _image_aes_key(image_item: dict[str, Any]) -> str | None:
        aes_key_hex = str(image_item.get("aeskey") or "").strip()
        if aes_key_hex:
            try:
                return base64.b64encode(bytes.fromhex(aes_key_hex)).decode("ascii")
            except ValueError:
                return None
        media = image_item.get("media") or {}
        value = str(media.get("aes_key") or "").strip()
        return value or None

    @staticmethod
    def _extract_text(item_list: list[dict[str, Any]]) -> str:
        texts: list[str] = []
        for item in item_list:
            if item.get("type") != ITEM_TEXT:
                continue
            text_item = item.get("text_item") or {}
            text = str(text_item.get("text") or "").strip()
            if text:
                texts.append(text)
        return "\n".join(texts)

    @staticmethod
    def _optional_env(name: str | None) -> str:
        if not name:
            return ""
        return str(os.getenv(name, "")).strip()

    def _split_text(self, text: str) -> list[str]:
        if not self.config.split_multiline_messages:
            return [text]
        return [line.strip() for line in text.splitlines() if line.strip()] or [text]

    @staticmethod
    def _conversation_target(message: dict[str, Any], sender_id: str) -> tuple[str, str]:
        room_id = str(message.get("room_id") or message.get("chat_room_id") or "").strip()
        if room_id:
            return "group", room_id
        return "dm", sender_id

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

    def _metadata_media_requests(self, metadata: dict | None) -> list[OutboundMediaRequest]:
        if not isinstance(metadata, dict):
            return []
        requests: list[OutboundMediaRequest] = []
        raw_items = metadata.get("outbound_attachments")
        if not isinstance(raw_items, list):
            return requests
        for raw_item in raw_items:
            raw_path = ""
            force_file_attachment = False
            attachment_type = ""
            if isinstance(raw_item, str):
                raw_path = raw_item
            elif isinstance(raw_item, dict):
                raw_path = str(
                    raw_item.get("path")
                    or raw_item.get("file_path")
                    or raw_item.get("local_path")
                    or ""
                ).strip()
                force_file_attachment = bool(raw_item.get("force_file_attachment"))
                attachment_type = str(raw_item.get("type") or "").strip().lower()
            if not raw_path:
                continue
            local_path = extract_local_file_path(raw_path)
            if local_path is None:
                continue
            requests.append(
                OutboundMediaRequest(
                    path=local_path,
                    force_file_attachment=force_file_attachment or attachment_type == "file",
                    media_kind_override=attachment_type if attachment_type in {"image", "video", "voice", "file"} else None,
                )
            )
        return requests

    async def _poll_loop(self) -> None:
        logger.info(f"WeChat poll loop started account={self._account_id}")
        while self.started:
            response = await self._get_updates_func(
                session=self._poll_session,
                base_url=self._base_url,
                token=self._token,
                sync_buf=self._sync_buf,
                timeout_ms=35_000,
            )
            next_sync_buf = str(response.get("get_updates_buf") or "").strip()
            if next_sync_buf:
                self._sync_buf = next_sync_buf
            messages = [message for message in response.get("msgs") or [] if isinstance(message, dict)]
            if messages:
                logger.info(
                    "WeChat poll batch received "
                    f"account={self._account_id} message_count={len(messages)}"
                )
            for message in messages:
                try:
                    await self._process_message(message)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "WeChat poll loop failed to process message "
                        f"message_id={str(message.get('message_id') or '').strip()}"
                    )
        logger.info(f"WeChat poll loop stopped account={self._account_id}")

    @staticmethod
    def _truncate_log_text(value: object, *, limit: int = LOG_TEXT_TRUNCATE_LIMIT) -> str:
        text = str(value or "").replace("\n", "\\n").strip()
        if len(text) <= limit:
            return text
        return f"{text[:limit]}..."

    def _remember_seen_message_id(self, message_id: str) -> bool:
        normalized = str(message_id or "").strip()
        if not normalized:
            return False
        if normalized in self._seen_message_ids:
            return True
        self._seen_message_ids[normalized] = None
        while len(self._seen_message_ids) > DEDUP_MAX_SIZE:
            self._seen_message_ids.pop(next(iter(self._seen_message_ids)))
        return False

    def _build_session(self) -> object:
        if aiohttp is None:
            return _NoopSession()
        return aiohttp.ClientSession(trust_env=True)


class _NoopSession:
    def post(self, *args, **kwargs):
        raise RuntimeError("aiohttp is required for default WeChat network operations")

    def get(self, *args, **kwargs):
        raise RuntimeError("aiohttp is required for default WeChat network operations")


def _json_dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _endpoint_url(base_url: str, endpoint: str) -> str:
    return f"{base_url.rstrip('/')}/{endpoint}"


def _headers(token: str, body: str) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Content-Length": str(len(body.encode("utf-8"))),
        "X-WECHAT-UIN": uuid.uuid4().hex[:16],
        "iLink-App-Id": ILINK_APP_ID,
        "iLink-App-ClientVersion": ILINK_APP_CLIENT_VERSION,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _build_timeout(timeout_ms: int):
    if aiohttp is None:
        return timeout_ms / 1000
    return aiohttp.ClientTimeout(total=timeout_ms / 1000)


def _build_timeout_seconds(timeout_seconds: float):
    if aiohttp is None:
        return timeout_seconds
    return aiohttp.ClientTimeout(total=timeout_seconds)


async def _close_session(session: object | None) -> None:
    if session is None:
        return
    close = getattr(session, "close", None)
    if close is None:
        return
    result = close()
    if asyncio.iscoroutine(result):
        await result
