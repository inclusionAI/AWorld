from __future__ import annotations

import asyncio
import base64
import hashlib
from inspect import isawaitable
import json
import logging
import mimetypes
import os
import re
import threading
import time
from pathlib import Path
from urllib.parse import unquote, urlparse
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
from aworld_gateway.cron_push import CronPushBindingStore, CronPushBridge
from aworld_gateway.http.artifact_service import ArtifactService
from aworld_gateway.logging import get_gateway_logger
from aworld_cli.core.command_bridge import CommandBridge
from aworld.logs.util import logger

DINGTALK_API = "https://api.dingtalk.com"
OAPI_API = "https://oapi.dingtalk.com"
MEDIA_MAX_BYTES = 20 * 1024 * 1024
AI_CARD_REQUEST_RETRIES = 2
AI_CARD_RETRY_DELAY_SECONDS = 0.3
AI_CARD_STREAM_UPDATE_INTERVAL_SECONDS = 0.3
PROCESSING_ACK_DELAY_SECONDS = 0.8
CALLBACK_DEDUPE_WINDOW_SECONDS = 15.0
PROCESSING_ACK_TEXT = "已收到，正在处理。"
WEBHOOK_HASH_DIGEST_LENGTH = 10
TRIVIAL_SHORT_REQUEST_MAX_CHARS = 12
COMPLEX_REQUEST_KEYWORDS = (
    "分析",
    "收集",
    "整理",
    "生成",
    "报告",
    "调研",
    "搜索",
    "新闻",
    "html",
    "trajectory.log",
    ".html",
    "提醒",
    "定时",
    "cron",
)
TASK_REQUEST_KEYWORDS = (
    "帮",
    "看",
    "查",
    "排查",
    "解释",
    "总结",
    "写",
    "改",
    "修",
    "处理",
    "解决",
    "翻译",
    "报错",
)
EXECUTION_GUARDRAIL_TEXT = """\
执行要求:
 - 严格保留用户原始请求中的文件或日志名、时间范围、输出格式与交付动作。
 - 如需拆分或改写任务，不得遗漏这些明确约束。
 - 如果用户要求生成 HTML 或其他文件产物，必须生成对应产物，或在最终答复中明确说明阻塞原因。
"""
MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")
LOCAL_PATH_RE = re.compile(r"^(?:/|~|[A-Za-z]:[\\/])")
INLINE_CODE_LOCAL_REF_RE = re.compile(
    r"`(artifact://[^\s`]+|attachment://[^\s`]+|file://[^\s`]+|MEDIA:[^\s`]+|(?:/|~)[^\s`]+|[A-Za-z]:[\\/][^\s`]+)`"
)
PLAIN_LOCAL_REF_RE = re.compile(
    r"(artifact://[^\s<>)]+|attachment://[^\s<>)]+|file://[^\s<>)]+|MEDIA:[^\s<>)]+|(?:/|~)[^\s<>)]+|[A-Za-z]:[\\/][^\s<>)]+)"
)
gateway_logger = get_gateway_logger("dingding.connector")


class DingTalkConnector:
    def __init__(
        self,
        *,
        config: DingdingChannelConfig,
        bridge: AworldDingdingBridge,
        stream_module,
        http_client: httpx.AsyncClient | None = None,
        thread_cls: type[threading.Thread] = threading.Thread,
        artifact_service: object | None = None,
        command_bridge: CommandBridge | None = None,
    ) -> None:
        self._config = config
        self._bridge = bridge
        self._stream_module = stream_module
        self._http = http_client or httpx.AsyncClient(timeout=60.0)
        self._thread_cls = thread_cls
        self._artifact_service = artifact_service
        self._command_bridge = command_bridge or CommandBridge()
        self._session_ids: dict[str, str] = {}
        self._conversation_active_runs: dict[str, int] = {}
        self._conversation_state_lock = threading.Lock()
        self._client = None
        self._stream_thread: threading.Thread | None = None
        self._access_token: str | None = None
        self._access_token_expiry: float = 0.0
        self._oapi_access_token: str | None = None
        self._oapi_access_token_expiry: float = 0.0
        self._callback_fingerprints: dict[str, float] = {}
        self._callback_fingerprint_lock = threading.Lock()
        self._background_tasks: set[asyncio.Task[None]] = set()
        binding_store_path = self._binding_store_path()
        self._migrate_legacy_cron_bindings(binding_store_path)
        self._cron_push_bridge = CronPushBridge(
            binding_store=CronPushBindingStore(binding_store_path)
        )
        self._cron_push_bridge.register_sender("dingtalk", self._send_cron_push_text)
        self._cron_scheduler = None
        self._cron_scheduler_started_by_connector = False

    async def start(self) -> None:
        gateway_logger.info(
            "DingTalk connector starting "
            f"workspace_dir={self._config.workspace_dir or ''} "
            f"client_id_env={self._config.client_id_env or ''}"
        )
        credential = self._stream_module.Credential(
            self._required_env(self._config.client_id_env),
            self._required_env(self._config.client_secret_env),
        )
        self._client = self._stream_module.DingTalkStreamClient(credential)
        connector = self

        class _MessageHandler(self._stream_module.ChatbotHandler):
            async def process(self, callback):
                payload = getattr(callback, "data", callback)
                connector._schedule_callback(payload)
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
        await self._prepare_cron_runtime()
        await self._validate_upstream_auth()

        start_forever = getattr(self._client, "start_forever", None)
        if callable(start_forever):
            self._stream_thread = self._thread_cls(
                target=self._run_stream_forever,
                name="aworld-gateway-dingtalk-stream",
                daemon=True,
            )
            self._stream_thread.start()
            gateway_logger.info(
                "DingTalk stream client started mode=start_forever thread=True"
            )
            return

        start = getattr(self._client, "start", None)
        if callable(start):
            gateway_logger.info("DingTalk stream runner entering mode=start")
            try:
                start_result = start()
                if isawaitable(start_result):
                    await start_result
                gateway_logger.info(
                    "DingTalk stream client started mode=start thread=False"
                )
            except Exception as exc:
                gateway_logger.exception(
                    "DingTalk stream runner failed "
                    f"mode=start error={exc}"
                )
                raise
            finally:
                gateway_logger.info("DingTalk stream runner exited mode=start")

    async def stop(self) -> None:
        gateway_logger.info("DingTalk connector stopping")
        for task in list(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()
        if self._cron_scheduler is not None:
            self._cron_push_bridge.uninstall_scheduler_sink(self._cron_scheduler)
        if self._cron_scheduler_started_by_connector and self._cron_scheduler is not None:
            stop = getattr(self._cron_scheduler, "stop", None)
            if callable(stop):
                stop_result = stop()
                if isawaitable(stop_result):
                    await stop_result
        self._cron_scheduler_started_by_connector = False
        self._cron_scheduler = None
        if self._client is not None:
            for method_name in ("stop", "close", "shutdown"):
                stop_method = getattr(self._client, method_name, None)
                if not callable(stop_method):
                    continue
                stop_result = stop_method()
                if isawaitable(stop_result):
                    await stop_result
                break
        self._cron_scheduler = None
        await self._http.aclose()
        gateway_logger.info("DingTalk connector stopped")

    async def handle_callback(self, callback_payload) -> None:
        data = self._parse_data(callback_payload)
        if not data:
            gateway_logger.info("DingTalk callback skipped reason=invalid_payload")
            return
        self._log_callback_received(data)
        if self._should_skip_duplicate_callback(data):
            gateway_logger.info("DingTalk callback skipped reason=duplicate")
            return
        await self._handle_callback_data(data)

    async def _handle_callback_data(self, data: dict) -> None:
        session_webhook = str(data.get("sessionWebhook") or "").strip()
        if not session_webhook:
            gateway_logger.info("DingTalk callback skipped reason=missing_session_webhook")
            return

        sender_id = str(data.get("senderStaffId") or data.get("senderId") or "").strip()
        if not sender_id:
            gateway_logger.info("DingTalk callback skipped reason=missing_sender")
            return

        message = self._extract_message(data)
        user_text = message.text.strip()
        if not user_text and not message.attachments:
            gateway_logger.info("DingTalk callback skipped reason=empty_message")
            return

        conversation_key = str(data.get("conversationId") or sender_id).strip()
        if user_text.lower() in {command.lower() for command in NEW_SESSION_COMMANDS}:
            self._session_ids[conversation_key] = self._new_session_id(conversation_key)
            await self.send_text(
                session_webhook=session_webhook,
                text="✨ 已开启新会话，之前的上下文已清空。",
            )
            return

        session_id, isolated_from_inflight = self._acquire_session_for_message(
            conversation_key
        )

        try:
            logger.info(
                "DingTalk inbound message "
                f"conversation={conversation_key} sender={sender_id} session={session_id} "
                f"text={self._truncate_log_text(user_text, limit=300)}"
            )
            self._mirror_business_log_to_std_logging(
                "DingTalk inbound message "
                f"conversation={conversation_key} sender={sender_id} session={session_id} "
                f"text={self._truncate_log_text(user_text, limit=300)}"
            )
            if isolated_from_inflight:
                self._log_business_info(
                    "DingTalk concurrent turn isolated "
                    f"conversation={conversation_key} session={session_id}"
                )

            async def _execute_prompt_command(
                *,
                prompt: str,
                allowed_tools: list[str] | None,
                on_output=None,
            ) -> str:
                await self._run_message_round(
                    session_webhook=session_webhook,
                    session_id=session_id,
                    text=prompt,
                    request_text=message.text,
                    has_attachments=bool(message.attachments),
                    data=data,
                    allowed_tools=allowed_tools,
                )
                return ""

            command_result = await self._command_bridge.execute(
                text=user_text,
                cwd=str(Path.cwd()),
                session_id=session_id,
                prompt_executor=_execute_prompt_command,
            )
            if command_result.handled:
                if command_result.text:
                    await self.send_text(
                        session_webhook=session_webhook,
                        text=command_result.text,
                    )
                return

            enriched_text = self._append_user_context_to_text(message.text, data)
            user_input = await self._build_llm_user_input(
                ExtractedMessage(text=enriched_text, attachments=message.attachments),
                self._attachment_session_key(data, sender_id),
            )

            await self._run_message_round(
                session_webhook=session_webhook,
                session_id=session_id,
                text=user_input,
                request_text=message.text,
                has_attachments=bool(message.attachments),
                data=data,
            )
        finally:
            self._release_session_for_message(conversation_key)

    def _schedule_callback(self, callback_payload) -> None:
        data = self._parse_data(callback_payload)
        if not data:
            gateway_logger.info("DingTalk callback skipped reason=invalid_payload")
            return
        self._log_callback_received(data)
        if self._should_skip_duplicate_callback(data):
            gateway_logger.info("DingTalk callback skipped reason=duplicate")
            return

        task = asyncio.create_task(self._handle_callback_data(data))
        self._background_tasks.add(task)
        task.add_done_callback(self._finalize_background_task)

    async def send_text(self, *, session_webhook: str, text: str) -> None:
        target = self._describe_webhook(session_webhook)
        gateway_logger.info(
            "DingTalk outbound message sending "
            f"target={target} chars={len(text)}"
        )
        try:
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
            gateway_logger.info(
                "DingTalk outbound message sent "
                f"target={target} chars={len(text)}"
            )
        except Exception as exc:
            gateway_logger.exception(
                "DingTalk outbound message failed "
                f"target={target} chars={len(text)} error={exc}"
            )
            raise

    async def _run_message_round(
        self,
        *,
        session_webhook: str,
        session_id: str,
        text: str | list[dict[str, Any]],
        request_text: str,
        has_attachments: bool,
        data: dict,
        allowed_tools: list[str] | None = None,
    ) -> str:
        active_card = (
            await self._try_create_ai_card(data)
            if self._config.enable_ai_card
            else None
        )
        if not self._config.enable_ai_card:
            self._log_ai_card_unavailable(reason="disabled", data=data)
        if active_card is not None:
            self._log_business_info(
                "DingTalk AI Card active "
                f"session={session_id} card={active_card.card_instance_id}"
            )

        ack_sent = False
        def mark_ack_sent() -> None:
            nonlocal ack_sent
            ack_sent = True

        delayed_ack_task: asyncio.Task[None] | None = None
        if active_card is None:
            if self._should_send_processing_ack(
                request_text,
                has_attachments=has_attachments,
            ):
                await self.send_text(session_webhook=session_webhook, text=PROCESSING_ACK_TEXT)
                ack_sent = True
            elif self._should_delay_processing_ack(
                request_text,
                has_attachments=has_attachments,
            ):
                delayed_ack_task = asyncio.create_task(
                    self._send_processing_ack_after_delay(
                        session_webhook=session_webhook,
                        on_sent=mark_ack_sent,
                    )
                )
        streamed_parts: list[str] = []
        last_card_push_at = -AI_CARD_STREAM_UPDATE_INTERVAL_SECONDS
        streamed_chunk_count = 0
        card_push_count = 0

        async def on_text_chunk(chunk: str) -> None:
            nonlocal last_card_push_at, streamed_chunk_count, card_push_count
            if not chunk:
                return
            streamed_chunk_count += 1
            streamed_parts.append(chunk)
            if active_card is not None:
                now = self._now_for_ai_card_stream()
                if now - last_card_push_at >= AI_CARD_STREAM_UPDATE_INTERVAL_SECONDS:
                    if await self._stream_ai_card(
                        active_card,
                        "".join(streamed_parts),
                        finished=False,
                    ):
                        last_card_push_at = now
                        card_push_count += 1

        async def on_output(output) -> None:
            summary = self._summarize_runtime_output_for_log(output)
            if summary:
                logger.info(
                    "DingTalk runtime output "
                    f"session={session_id} "
                    f"conversation={str(data.get('conversationId') or data.get('senderStaffId') or data.get('senderId') or '').strip()} "
                    f"{summary}"
                )
                self._mirror_business_log_to_std_logging(
                    "DingTalk runtime output "
                    f"session={session_id} "
                    f"conversation={str(data.get('conversationId') or data.get('senderStaffId') or data.get('senderId') or '').strip()} "
                    f"{summary}"
                )
            self._cron_push_bridge.bind_output(
                output,
                {
                    "channel": "dingtalk",
                    "conversation_id": str(data.get("conversationId") or "").strip(),
                    "sender_id": str(
                        data.get("senderStaffId") or data.get("senderId") or ""
                    ).strip(),
                    "target": {"session_webhook": session_webhook},
                },
            )

        try:
            bridge_run_kwargs = {
                "agent_id": self._resolve_agent_id(),
                "session_id": session_id,
                "text": text,
                "on_text_chunk": on_text_chunk,
                "on_output": on_output,
            }
            if allowed_tools is not None:
                bridge_run_kwargs["allowed_tools"] = allowed_tools
            result = await self._bridge.run(
                **bridge_run_kwargs,
            )
        except Exception as exc:
            await self._finalize_processing_ack_task(delayed_ack_task)
            await self._send_error_to_client(
                session_webhook=session_webhook,
                card=active_card,
                text=f"抱歉，调用 Agent 失败：{exc}",
            )
            return ""

        await self._finalize_processing_ack_task(delayed_ack_task)
        final_text, pending_files = await self._process_local_media_links(result.text)
        display_text = final_text or ("✅ 媒体已发送" if pending_files else "（空响应）")
        logger.info(
            "DingTalk final reply "
            f"session={session_id} text={self._truncate_log_text(display_text, limit=500)}"
        )
        self._mirror_business_log_to_std_logging(
            "DingTalk final reply "
            f"session={session_id} text={self._truncate_log_text(display_text, limit=500)}"
        )

        fallback_to_text = active_card is None
        if active_card is not None:
            if await self._finish_ai_card(active_card, display_text):
                self._log_business_info(
                    "DingTalk stream summary "
                    f"session={session_id} chunks={streamed_chunk_count} "
                    f"card_updates={card_push_count} ack_sent={ack_sent} "
                    "fallback_to_text=False"
                )
                await self._send_pending_files(session_webhook, pending_files)
                return display_text
            fallback_to_text = True
            self._log_business_info(
                "DingTalk AI Card finalize failed "
                f"session={session_id} card={active_card.card_instance_id} "
                "fallback=text"
            )

        await self.send_text(session_webhook=session_webhook, text=display_text)
        self._log_business_info(
            "DingTalk stream summary "
            f"session={session_id} chunks={streamed_chunk_count} "
            f"card_updates={card_push_count} ack_sent={ack_sent} "
            f"fallback_to_text={fallback_to_text}"
        )
        await self._send_pending_files(session_webhook, pending_files)
        return display_text

    def _resolve_agent_id(self) -> str:
        agent_id = str(self._config.default_agent_id or "").strip()
        if agent_id:
            return agent_id
        raise ValueError("No agent id configured for DingTalk channel.")

    @staticmethod
    def _now_for_ai_card_stream() -> float:
        return time.monotonic()

    @staticmethod
    def _processing_ack_delay_seconds() -> float:
        return PROCESSING_ACK_DELAY_SECONDS

    def _binding_store_path(self) -> Path:
        workspace_dir = str(self._config.workspace_dir or "").strip()
        if workspace_dir:
            base_dir = Path(workspace_dir).expanduser()
            if base_dir.is_absolute():
                return base_dir.parent / "cron-bindings.json"
        return Path(".aworld/gateway/dingding/cron-bindings.json").resolve()

    @staticmethod
    def _migrate_legacy_cron_bindings(file_path: Path) -> None:
        if not file_path.exists():
            return

        try:
            raw = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        if not isinstance(raw, dict):
            return

        migrated: dict[str, dict[str, object]] = {}
        changed = False
        for job_id, value in raw.items():
            normalized_job_id = str(job_id or "").strip()
            if not normalized_job_id or not isinstance(value, dict):
                continue

            if isinstance(value.get("channel"), str) and isinstance(value.get("target"), dict):
                migrated[normalized_job_id] = dict(value)
                continue

            session_webhook = str(value.get("session_webhook") or "").strip()
            if not session_webhook:
                continue

            changed = True
            migrated[normalized_job_id] = {
                "job_id": str(value.get("job_id") or normalized_job_id),
                "channel": "dingtalk",
                "conversation_id": str(value.get("conversation_id") or "").strip(),
                "sender_id": str(value.get("sender_id") or "").strip(),
                "target": {"session_webhook": session_webhook},
            }

        if not changed:
            return

        file_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = file_path.with_name(f"{file_path.name}.tmp")
        temp_path.write_text(
            json.dumps(migrated, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(file_path)

    @staticmethod
    def _mirror_business_log_to_std_logging(message: str) -> None:
        logging.getLogger("aworld").info(message)

    def _log_business_info(self, message: str) -> None:
        logger.info(message)
        self._mirror_business_log_to_std_logging(message)

    @staticmethod
    def _truncate_log_text(value, *, limit: int = 300) -> str:
        if value is None:
            return ""
        text = value if isinstance(value, str) else str(value)
        text = " ".join(text.split())
        if len(text) <= limit:
            return text
        return f"{text[: limit - 3]}..."

    @staticmethod
    def _describe_webhook(session_webhook: str) -> str:
        normalized = str(session_webhook or "").strip()
        if not normalized:
            return "missing"
        digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:WEBHOOK_HASH_DIGEST_LENGTH]
        parsed = urlparse(normalized)
        host = parsed.netloc or "unknown-host"
        path = parsed.path or "/"
        return f"{host}{path}#{digest}"

    @classmethod
    def _summarize_runtime_output_for_log(cls, output) -> str:
        output_type_getter = getattr(output, "output_type", None)
        output_type = output_type_getter() if callable(output_type_getter) else type(output).__name__
        parts: list[str] = [f"type={output_type}"]

        tool_name = str(getattr(output, "tool_name", "") or "").strip()
        if tool_name:
            parts.append(f"tool={tool_name}")

        status = str(getattr(output, "status", "") or "").strip()
        if status:
            parts.append(f"status={status}")

        name = str(getattr(output, "alias_name", "") or getattr(output, "name", "") or "").strip()
        if name:
            parts.append(f"name={cls._truncate_log_text(name, limit=120)}")

        for candidate in (
            getattr(output, "response", None),
            getattr(output, "content", None),
            getattr(output, "payload", None),
            getattr(output, "data", None),
        ):
            if candidate in (None, ""):
                continue
            if isinstance(candidate, (dict, list)):
                try:
                    serialized = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
                except (TypeError, ValueError):
                    serialized = str(candidate)
                parts.append(f"data={cls._truncate_log_text(serialized, limit=300)}")
                break
            text = cls._truncate_log_text(candidate, limit=300)
            if text:
                parts.append(f"data={text}")
                break

        return " ".join(part for part in parts if part)

    async def _prepare_cron_runtime(self) -> None:
        try:
            from aworld.core.scheduler import get_scheduler
            from aworld_cli.core.agent_registry import LocalAgentRegistry
        except Exception as exc:
            logger.warning(f"Failed to import cron runtime dependencies for DingTalk: {exc}")
            return

        scheduler = get_scheduler()
        self._cron_scheduler = scheduler

        executor = getattr(scheduler, "executor", None)
        if executor is not None:
            if hasattr(executor, "set_swarm_resolver"):
                async def resolve_swarm(agent_name: str):
                    agent = LocalAgentRegistry.get_agent(agent_name)
                    if agent is None:
                        return None
                    return await AworldDingdingBridge._get_swarm_with_context_fallback(
                        agent,
                        refresh=agent_name == "Aworld",
                    )

                executor.set_swarm_resolver(resolve_swarm)

            if hasattr(executor, "set_default_agent_name"):
                try:
                    executor.set_default_agent_name(self._resolve_agent_id())
                except ValueError:
                    pass

        self._cron_push_bridge.install_scheduler_sink(scheduler)

        if getattr(scheduler, "running", False):
            return

        start = getattr(scheduler, "start", None)
        if not callable(start):
            return

        start_result = start()
        if isawaitable(start_result):
            await start_result
        self._cron_scheduler_started_by_connector = True

    def _should_skip_duplicate_callback(self, data: dict) -> bool:
        callback_key = self._build_callback_key(data)
        if not callback_key:
            return False

        now = time.monotonic()
        with self._callback_fingerprint_lock:
            expired_before = now - CALLBACK_DEDUPE_WINDOW_SECONDS
            expired_keys = [
                key
                for key, seen_at in self._callback_fingerprints.items()
                if seen_at < expired_before
            ]
            for key in expired_keys:
                self._callback_fingerprints.pop(key, None)

            previous_seen_at = self._callback_fingerprints.get(callback_key)
            self._callback_fingerprints[callback_key] = now

        return previous_seen_at is not None and now - previous_seen_at < CALLBACK_DEDUPE_WINDOW_SECONDS

    def _build_callback_key(self, data: dict) -> str | None:
        for field_name in (
            "messageId",
            "msgId",
            "msg_id",
            "message_id",
            "eventId",
            "event_id",
        ):
            value = str(data.get(field_name) or "").strip()
            if value:
                return f"id:{value}"

        try:
            payload_fingerprint = json.dumps(
                data,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
        except (TypeError, ValueError):
            return None

        if not payload_fingerprint:
            return None
        return f"payload:{hashlib.sha256(payload_fingerprint.encode('utf-8')).hexdigest()}"

    def _finalize_background_task(self, task: asyncio.Task[None]) -> None:
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            gateway_logger.warning(f"DingTalk callback task failed: {exc}")
            logger.warning(f"DingTalk callback task failed: {exc}")

    async def _send_cron_push_text(self, binding, text: str, notification) -> None:
        target = binding.get("target") if isinstance(binding, dict) else None
        if not isinstance(target, dict):
            return

        session_webhook = str(target.get("session_webhook") or "").strip()
        if not session_webhook:
            return

        await self.send_text(session_webhook=session_webhook, text=text)

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

    def _log_callback_received(self, data: dict) -> None:
        gateway_logger.info(
            "DingTalk callback received "
            f"conversation={str(data.get('conversationId') or '').strip() or 'unknown'} "
            f"sender={str(data.get('senderStaffId') or data.get('senderId') or '').strip() or 'unknown'} "
            f"message_id={str(data.get('messageId') or data.get('msgId') or data.get('eventId') or '').strip() or 'unknown'} "
            f"has_session_webhook={bool(str(data.get('sessionWebhook') or '').strip())}"
        )

    @staticmethod
    def _new_session_id(conversation_key: str) -> str:
        return f"dingtalk_{conversation_key}_{uuid4().hex[:8]}"

    def _acquire_session_for_message(self, conversation_key: str) -> tuple[str, bool]:
        with self._conversation_state_lock:
            active_runs = self._conversation_active_runs.get(conversation_key, 0)
            current_session_id = self._session_ids.get(conversation_key)

            if not current_session_id:
                session_id = self._new_session_id(conversation_key)
                self._session_ids[conversation_key] = session_id
            else:
                session_id = current_session_id
            isolated_from_inflight = active_runs > 0

            self._conversation_active_runs[conversation_key] = active_runs + 1
            return session_id, isolated_from_inflight

    def _release_session_for_message(self, conversation_key: str) -> None:
        with self._conversation_state_lock:
            active_runs = self._conversation_active_runs.get(conversation_key, 0)
            if active_runs <= 1:
                self._conversation_active_runs.pop(conversation_key, None)
                return
            self._conversation_active_runs[conversation_key] = active_runs - 1

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

    async def _build_llm_user_input(
        self,
        message: ExtractedMessage,
        session_key: str,
    ) -> str | list[dict[str, Any]]:
        text = message.text.strip()
        if not message.attachments:
            return text

        local_paths: list[str] = []
        for attachment in message.attachments:
            local_path = await self._download_attachment(attachment, session_key)
            if local_path:
                local_paths.append(local_path)

        if not local_paths:
            return text

        multimodal_parts: list[dict[str, Any]] = []
        fallback_paths: list[str] = []
        for path in local_paths:
            multimodal_part = await self._build_multimodal_part(path)
            if multimodal_part is None:
                fallback_paths.append(path)
                continue
            multimodal_parts.append(multimodal_part)

        attachment_prompt = self._build_attachment_prompt(fallback_paths)
        text_payload = self._merge_text_with_attachment_prompt(text, attachment_prompt)
        if not multimodal_parts:
            return text_payload

        parts: list[dict[str, Any]] = []
        if text_payload:
            parts.append({"type": "text", "text": text_payload})
        parts.extend(multimodal_parts)
        return parts

    @staticmethod
    def _build_attachment_prompt(paths: list[str]) -> str:
        if not paths:
            return ""
        lines = ["附件列表:", *[f"  - {path}" for path in paths]]
        return "\n".join(lines)

    @staticmethod
    def _merge_text_with_attachment_prompt(text: str, attachment_prompt: str) -> str:
        if text and attachment_prompt:
            return f"{text}\n\n{attachment_prompt}"
        return text or attachment_prompt

    @staticmethod
    def _append_user_context_to_text(text: str, data: dict[str, Any]) -> str:
        user_id = str(data.get("senderId") or data.get("senderStaffId") or "").strip()
        user_name = str(data.get("senderNick") or "").strip()
        conversation_id = str(data.get("conversationId") or "").strip()
        robot_code = str(data.get("robotCode") or "").strip()
        context = f"""\
会话附加信息:
 - userId: {user_id or 'unknown'}
 - userName: {user_name or 'unknown'}
 - conversationId: {conversation_id or 'unknown'}
 - robotCode: {robot_code or 'unknown'}
"""
        return f"{text}\n{context}\n{EXECUTION_GUARDRAIL_TEXT}"

    @staticmethod
    def _should_send_processing_ack(
        text: str,
        *,
        has_attachments: bool = False,
    ) -> bool:
        if has_attachments:
            return True

        normalized = str(text or "").strip().lower()
        if len(normalized) >= 40:
            return True

        return any(keyword in normalized for keyword in COMPLEX_REQUEST_KEYWORDS)

    @classmethod
    def _should_delay_processing_ack(
        cls,
        text: str,
        *,
        has_attachments: bool = False,
    ) -> bool:
        if has_attachments:
            return False

        normalized = str(text or "").strip().lower()
        if not normalized:
            return False
        if cls._should_send_processing_ack(normalized, has_attachments=has_attachments):
            return False
        if len(normalized) > TRIVIAL_SHORT_REQUEST_MAX_CHARS:
            return True

        return any(keyword in normalized for keyword in TASK_REQUEST_KEYWORDS)

    async def _send_processing_ack_after_delay(
        self,
        *,
        session_webhook: str,
        on_sent,
    ) -> None:
        try:
            await asyncio.sleep(self._processing_ack_delay_seconds())
            await self.send_text(session_webhook=session_webhook, text=PROCESSING_ACK_TEXT)
            on_sent()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive logging
            self._log_business_info(
                "DingTalk delayed processing ack failed "
                f"error={type(exc).__name__}"
            )

    @staticmethod
    async def _finalize_processing_ack_task(task: asyncio.Task[None] | None) -> None:
        if task is None:
            return
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _build_multimodal_part(self, local_path: str) -> dict[str, Any] | None:
        mime_type, _ = mimetypes.guess_type(local_path)
        if not mime_type or not mime_type.startswith("image/"):
            return None
        image_data = await self._build_image_data(local_path, mime_type)
        if not image_data:
            return None
        return {"type": "image_url", "image_url": {"url": image_data}}

    @staticmethod
    async def _build_image_data(local_path: str, mime_type: str) -> str | None:
        try:
            image_bytes = await asyncio.to_thread(Path(local_path).read_bytes)
        except Exception:
            return None
        if not image_bytes:
            return None
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    async def _download_attachment(
        self,
        attachment: IncomingAttachment,
        session_key: str,
    ) -> str | None:
        download_url = await self._get_attachment_download_url(attachment.download_code)
        if not download_url:
            return None

        target_dir = self._attachment_root_dir() / session_key
        await asyncio.to_thread(target_dir.mkdir, parents=True, exist_ok=True)

        try:
            response = await self._http.get(download_url)
            response.raise_for_status()
            target_path = self._build_attachment_target_path(
                target_dir=target_dir,
                original_name=attachment.file_name,
                download_url=download_url,
                content_type=response.headers.get("content-type", ""),
            )
            await asyncio.to_thread(target_path.write_bytes, response.content)
            return str(target_path.resolve())
        except Exception:
            return None

    async def _get_attachment_download_url(self, download_code: str) -> str | None:
        token = await self._get_access_token()
        headers = {
            "x-acs-dingtalk-access-token": token,
            "Content-Type": "application/json",
        }
        payload = {
            "downloadCode": download_code,
            "robotCode": self._required_env(self._config.client_id_env),
        }
        try:
            response = await self._http.post(
                f"{DINGTALK_API}/v1.0/robot/messageFiles/download",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            download_url = str(response.json().get("downloadUrl") or "").strip()
            return download_url or None
        except Exception:
            return None

    def _attachment_root_dir(self) -> Path:
        workspace_dir = str(self._config.workspace_dir or "").strip()
        if workspace_dir:
            return Path(workspace_dir).expanduser().resolve() / "dingtalk"
        return Path(".aworld/gateway/dingding/attachments").resolve()

    @staticmethod
    def _sanitize_filename(file_name: str) -> str:
        name = os.path.basename(file_name or "").strip() or "attachment"
        safe = "".join(
            ch if (ch.isalnum() or ch in {"-", "_", "."}) else "_" for ch in name
        )
        return safe or "attachment"

    @staticmethod
    def _attachment_session_key(data: dict[str, Any], sender_id: str) -> str:
        conversation_id = str(data.get("conversationId") or "").strip()
        raw = conversation_id or sender_id or "unknown"
        safe = "".join(ch if (ch.isalnum() or ch in {"-", "_"}) else "_" for ch in raw)
        return safe or "unknown"

    def _build_attachment_target_path(
        self,
        target_dir: Path,
        original_name: str,
        download_url: str,
        content_type: str,
    ) -> Path:
        safe_original_name = self._sanitize_filename(original_name).strip()
        if original_name.strip():
            candidate_name = safe_original_name
        else:
            from_url_name = self._extract_filename_from_url(download_url)
            if from_url_name:
                candidate_name = from_url_name
            else:
                ext = self._guess_extension(content_type)
                candidate_name = f"attachment_{int(time.time() * 1000)}_{uuid4().hex[:8]}{ext}"
        return self._dedupe_path(target_dir, candidate_name)

    def _extract_filename_from_url(self, download_url: str) -> str:
        path = unquote(urlparse(download_url).path or "")
        file_name = os.path.basename(path).strip()
        if not file_name:
            return ""
        return self._sanitize_filename(file_name)

    @staticmethod
    def _guess_extension(content_type: str) -> str:
        mime_type = (content_type or "").split(";", 1)[0].strip().lower()
        if not mime_type:
            return ".bin"
        ext = mimetypes.guess_extension(mime_type) or ""
        return ext if ext.startswith(".") and len(ext) > 1 else ".bin"

    @staticmethod
    def _dedupe_path(target_dir: Path, file_name: str) -> Path:
        base_name = Path(file_name).stem
        suffix = Path(file_name).suffix
        candidate = target_dir / file_name
        index = 1
        while candidate.exists():
            candidate = target_dir / f"{base_name}_{index}{suffix}"
            index += 1
        return candidate

    @staticmethod
    def _required_env(name: str | None) -> str:
        key = (name or "").strip()
        value = os.getenv(key, "").strip()
        if not value:
            raise ValueError(f"Missing required env var: {name}")
        return value

    async def _validate_upstream_auth(self) -> None:
        try:
            await self._get_access_token()
        except Exception as exc:
            gateway_logger.warning(
                "DingTalk upstream auth validation failed "
                f"client_id_env={self._config.client_id_env or ''} error={exc}"
            )
            return
        gateway_logger.info(
            "DingTalk upstream auth validated "
            f"client_id_env={self._config.client_id_env or ''}"
        )

    def _run_stream_forever(self) -> None:
        if self._client is None:
            gateway_logger.warning("DingTalk stream runner skipped reason=missing_client")
            return
        start_forever = getattr(self._client, "start_forever", None)
        if not callable(start_forever):
            gateway_logger.warning(
                "DingTalk stream runner skipped reason=start_forever_unavailable"
            )
            return
        gateway_logger.info("DingTalk stream runner entering mode=start_forever")
        try:
            start_forever()
        except Exception as exc:
            gateway_logger.exception(
                "DingTalk stream runner failed "
                f"mode=start_forever error={exc}"
            )
        finally:
            gateway_logger.info("DingTalk stream runner exited mode=start_forever")

    async def _get_access_token(self) -> str:
        now = time.time()
        if self._access_token and self._access_token_expiry - 60 > now:
            return self._access_token

        gateway_logger.info("DingTalk access token refresh requested")
        try:
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
            expires_in = int(data.get("expireIn", 7200))
            self._access_token = token
            self._access_token_expiry = now + expires_in
            gateway_logger.info(
                f"DingTalk access token refreshed expires_in_seconds={expires_in}"
            )
            return token
        except Exception as exc:
            gateway_logger.exception(
                f"DingTalk access token refresh failed error={exc}"
            )
            raise

    async def _get_oapi_access_token(self) -> str | None:
        now = time.time()
        if self._oapi_access_token and self._oapi_access_token_expiry - 60 > now:
            return self._oapi_access_token

        gateway_logger.info("DingTalk OAPI access token refresh requested")
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
                gateway_logger.warning(
                    "DingTalk OAPI access token rejected "
                    f"errcode={data.get('errcode')} errmsg={data.get('errmsg') or ''}"
                )
                return None
            token = str(data.get("access_token") or "").strip()
            if not token:
                gateway_logger.warning(
                    "DingTalk OAPI access token missing in response"
                )
                return None
            self._oapi_access_token = token
            expires_in = int(data.get("expires_in", 7200))
            self._oapi_access_token_expiry = now + expires_in
            gateway_logger.info(
                f"DingTalk OAPI access token refreshed expires_in_seconds={expires_in}"
            )
            return token
        except Exception as exc:
            gateway_logger.warning(
                f"DingTalk OAPI access token refresh failed error={exc}"
            )
            return None

    async def _process_local_media_links(
        self,
        content: str,
    ) -> tuple[str, list[PendingFileMessage]]:
        if not content:
            return content, []
        if (
            "artifact://" not in content
            and
            "attachment://" not in content
            and "file://" not in content
            and "MEDIA:" not in content
            and "/" not in content
            and "\\" not in content
            and "~" not in content
        ):
            return content, []

        oapi_token: str | None = None
        if self._config.enable_attachments:
            oapi_token = await self._get_oapi_access_token()

        result = content
        pending_files: list[PendingFileMessage] = []

        for match in list(MARKDOWN_IMAGE_RE.finditer(result)):
            full_match, alt_text, raw_url = match.group(0), match.group(1), match.group(2)
            local_path = self._extract_local_file_path(raw_url)
            if not local_path:
                continue
            if oapi_token and self._is_image_path(local_path):
                media_id = await self._upload_local_file_to_dingtalk(
                    local_path,
                    "image",
                    oapi_token,
                )
                if media_id:
                    result = result.replace(full_match, f"![{alt_text}]({media_id})", 1)
                    continue
            published_url = self._publish_local_reference(raw_url)
            if published_url:
                result = result.replace(full_match, f"![{alt_text}]({published_url})", 1)

        for match in list(MARKDOWN_LINK_RE.finditer(result)):
            full_match, _link_text, raw_url = match.group(0), match.group(1), match.group(2)
            local_path = self._extract_local_file_path(raw_url)
            if not local_path:
                continue
            if oapi_token:
                media_id = await self._upload_local_file_to_dingtalk(
                    local_path,
                    "file",
                    oapi_token,
                )
                if media_id:
                    pending_files.append(
                        PendingFileMessage(
                            media_id=media_id,
                            file_name=local_path.name,
                            file_type=local_path.suffix.lstrip(".").lower() or "bin",
                        )
                    )
                    result = result.replace(full_match, "", 1)
                    continue
            published_url = self._publish_local_reference(raw_url)
            if published_url:
                result = result.replace(full_match, f"[{match.group(1)}]({published_url})", 1)

        def _replace_plain_reference(match: re.Match[str]) -> str:
            raw_reference = match.group(0)
            if self._is_match_inside_markdown_link_or_image(result, match.start(), match.end()):
                return raw_reference
            candidate_reference, trailing_punctuation = self._split_trailing_plain_reference(
                raw_reference
            )
            published_url = self._publish_local_reference(candidate_reference)
            if not published_url:
                return raw_reference
            return f"{published_url}{trailing_punctuation}"

        def _replace_inline_code_reference(match: re.Match[str]) -> str:
            raw_reference = match.group(1)
            candidate_reference, trailing_punctuation = self._split_trailing_plain_reference(
                raw_reference
            )
            published_url = self._publish_local_reference(candidate_reference)
            if not published_url:
                return match.group(0)
            return f"{published_url}{trailing_punctuation}"

        result = INLINE_CODE_LOCAL_REF_RE.sub(_replace_inline_code_reference, result)
        result = PLAIN_LOCAL_REF_RE.sub(_replace_plain_reference, result)

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
            self._log_ai_card_unavailable(reason="disabled", data=data)
            return None
        card_template_env = str(self._config.card_template_id_env or "").strip()
        card_template_id = os.getenv(card_template_env, "").strip()
        if not card_template_id:
            self._log_ai_card_unavailable(
                reason="missing_card_template_id",
                data=data,
                extra=f"env={card_template_env or 'unset'}",
            )
            return None

        target = self._build_card_target(data)
        if target is None:
            self._log_ai_card_unavailable(
                reason=self._describe_missing_card_target_reason(data),
                data=data,
            )
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
            self._log_business_info(
                "DingTalk AI Card created "
                f"card={card_instance_id} target={self._extract_target_identifier(target)}"
            )
            deliver_resp = await self._request_with_retry(
                "POST",
                f"{DINGTALK_API}/v1.0/card/instances/deliver",
                headers=headers,
                json={"outTrackId": card_instance_id, "userIdType": 1, **target},
            )
            deliver_resp.raise_for_status()
            self._log_business_info(
                "DingTalk AI Card delivered "
                f"card={card_instance_id} target={self._extract_target_identifier(target)}"
            )
            return AICardInstance(
                card_instance_id=card_instance_id,
                access_token=token,
            )
        except Exception as exc:
            self._log_ai_card_unavailable(
                reason="create_or_deliver_failed",
                data=data,
                extra=f"error={type(exc).__name__}",
            )
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

    @staticmethod
    def _describe_missing_card_target_reason(data: dict) -> str:
        if str(data.get("conversationType") or "") == "2":
            conversation_id = str(data.get("conversationId") or "").strip()
            if not conversation_id:
                return "missing_group_conversation_id"
            return "missing_group_robot_code"
        if str(data.get("senderStaffId") or data.get("senderId") or "").strip():
            return "unknown_target"
        return "missing_sender_id"

    def _log_ai_card_unavailable(
        self,
        *,
        reason: str,
        data: dict,
        extra: str = "",
    ) -> None:
        conversation = str(
            data.get("conversationId")
            or data.get("senderStaffId")
            or data.get("senderId")
            or ""
        ).strip()
        details = (
            "DingTalk AI Card unavailable "
            f"reason={reason} conversation={conversation or 'unknown'}"
        )
        if extra:
            details = f"{details} {extra}"
        self._log_business_info(details)

    @staticmethod
    def _extract_target_identifier(target: dict) -> str:
        open_space_id = str(target.get("openSpaceId") or "").strip()
        if open_space_id:
            return open_space_id
        return "unknown"

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

    def _extract_local_file_path(self, raw_url: str) -> Path | None:
        candidate = raw_url.strip().strip("<>").strip("'").strip('"').strip("`")
        if not candidate:
            return None
        candidate = candidate.replace("\\ ", " ")
        if candidate.startswith("artifact://"):
            relative_path = unquote(candidate[len("artifact://") :]).strip().lstrip("/\\")
            workspace_dir = str(self._config.workspace_dir or "").strip()
            if not relative_path or not workspace_dir:
                return None
            workspace_path = Path(workspace_dir).expanduser()
            if not workspace_path.is_absolute():
                return None
            try:
                resolved_path = (workspace_path / relative_path).resolve()
                resolved_workspace = workspace_path.resolve()
            except OSError:
                return None
            if not resolved_path.is_relative_to(resolved_workspace):
                return None
            if not resolved_path.exists() or not resolved_path.is_file():
                return None
            return resolved_path
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

    def _publish_local_reference(self, raw_reference: str) -> str | None:
        if self._artifact_service is None:
            return None
        if not self._artifact_service_can_build_external_url():
            return None
        local_path = self._extract_local_file_path(raw_reference)
        if local_path is None:
            return None
        try:
            token = self._artifact_service.publish(local_path)
        except Exception:
            return None
        try:
            return self._artifact_service.build_external_url(token)
        except Exception:
            return None

    def _artifact_service_can_build_external_url(self) -> bool:
        if isinstance(self._artifact_service, ArtifactService):
            return getattr(self._artifact_service, "_public_base_url", None) is not None
        return True

    @staticmethod
    def _split_trailing_plain_reference(raw_reference: str) -> tuple[str, str]:
        candidate = raw_reference
        trailing = ""
        while candidate and candidate[-1] in {".", ","}:
            trailing = candidate[-1] + trailing
            candidate = candidate[:-1]
        return candidate, trailing

    @staticmethod
    def _is_match_inside_markdown_link_or_image(
        content: str,
        match_start: int,
        match_end: int,
    ) -> bool:
        return (
            match_start >= 2
            and content[match_start - 2 : match_start] == "]("
            and match_end < len(content)
            and content[match_end : match_end + 1] == ")"
        )

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
