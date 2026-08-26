import asyncio
import datetime
import json
import logging
import time
import traceback
from typing import Awaitable, Callable, List
from fastapi import Request, Response
import redis.asyncio as redis

from ..containers import ContainerServerManager

from ..utils.common_utils import get_remote_addr, get_pod_last_activate

from ..sessions import VpcSession, SessionId

from ..configs import (
    cluster_name,
    debug_mode,
    redis_url,
    MAX_LAST_ACTIVE_TIME_SEC,
    MAX_CREATED_AT_TIME_SEC,
)

logger = logging.getLogger(__name__)


class SessionRepo:
    def __init__(self):
        self.sessions: List[VpcSession] = []

    async def get_sessions(self) -> List[VpcSession]:
        return self.sessions

    async def update_vpc_session(self, vpc_session: VpcSession):
        pass

    async def remove_vpc_session(self, vpc_session: VpcSession):
        self.sessions.remove(vpc_session)


class SessionRedisRepo(SessionRepo):
    def __init__(self, redis_url: str):
        self._redis_client = redis.Redis.from_url(redis_url)
        self._sessions_key = f"{cluster_name}.vpc_sessions"

    async def initialize(self):
        await self._redis_client.ping()

    async def get_sessions(self) -> List[VpcSession]:
        """Get all VPC sessions from Redis"""
        sessions = []
        try:
            session_data = await self._redis_client.hgetall(self._sessions_key)
            for _, session_json in session_data.items():
                try:
                    if session_json:
                        session_data_str = session_json.decode("utf-8")
                        sessions.append(self._deserialize_vpc_session(session_data_str))
                except BaseException as e:
                    logger.error(
                        f"Error deserializing VPC session: {traceback.format_exc()}"
                    )
        except BaseException as e:
            logger.error(f"Error getting sessions: {traceback.format_exc()}")
        return sessions

    async def update_vpc_session(self, vpc_session: VpcSession):
        """Update VPC session in Redis"""
        try:
            session_json = self._serialize_vpc_session(vpc_session)
            await self._redis_client.hset(
                self._sessions_key, vpc_session.container_id, session_json
            )
        except BaseException as e:
            logger.error(f"Error updating VPC session: {traceback.format_exc()}")

    async def remove_vpc_session(self, vpc_session: VpcSession):
        """Remove VPC session from Redis"""
        try:
            await self._redis_client.hdel(self._sessions_key, vpc_session.container_id)
        except BaseException as e:
            logger.error(f"Error removing VPC session: {e}")

    def _serialize_vpc_session(self, vpc_session: VpcSession) -> str:
        """Serialize VpcSession to JSON string"""
        session_data = {
            "container_id": vpc_session.container_id,
            "container_ip_addr": vpc_session.container_ip_addr,
            "image_version": vpc_session.image_version,
            "mcp_port": vpc_session.mcp_port,
            "novnc_port": vpc_session.novnc_port,
            "channel_port": vpc_session.channel_port,
            "http_port": vpc_session.http_port,
            "container_server_id": vpc_session.container_server_id,
            "created_at": (
                vpc_session.created_at.timestamp() if vpc_session.created_at else None
            ),
            "last_active_at": (
                vpc_session.last_active_at.timestamp()
                if vpc_session.last_active_at
                else None
            ),
            "mcp_session_ids": vpc_session.mcp_session_ids,
            "session_ids": vpc_session.session_ids,
        }
        return json.dumps(session_data)

    def _deserialize_vpc_session(self, session_json: str) -> VpcSession:
        """Deserialize JSON string to VpcSession"""
        session_data = json.loads(session_json)
        vpc_session = VpcSession(
            container_id=session_data.get("container_id", ""),
            container_ip_addr=session_data.get("container_ip_addr", ""),
            image_version=session_data.get("image_version", ""),
            mcp_port=session_data.get("mcp_port", -1),
            novnc_port=session_data.get("novnc_port", -1),
            channel_port=session_data.get("channel_port", -1),
            http_port=session_data.get("http_port", -1),
            container_server_id=session_data.get("container_server_id", ""),
            created_at=(
                datetime.datetime.fromtimestamp(ca)
                if (ca := session_data.get("created_at", None))
                else None
            ),
            last_active_at=(
                datetime.datetime.fromtimestamp(la)
                if (la := session_data.get("last_active_at", None))
                else None
            ),
            mcp_session_ids=session_data.get("mcp_session_ids", []),
            session_ids=session_data.get("session_ids", []),
        )
        return vpc_session


class SessionConnectionManager:
    """Manages session connections to container servers"""

    def __init__(
        self,
        container_server_manager: ContainerServerManager,
        session_repo: SessionRepo = SessionRepo(),
    ):
        self._session_repo: SessionRepo = session_repo
        self.container_server_manager: ContainerServerManager = container_server_manager

    async def get_avaliable_session(self, session_id: SessionId) -> VpcSession | None:
        match_vpcs = [
            v for v in await self.get_sessions() if v.is_bind(session_id)
        ] or []
        sorted_vpcs = sorted(
            match_vpcs,
            key=lambda x: x.created_at.timestamp() if x.created_at else 0,
            reverse=True,
        )
        for v in sorted_vpcs:
            if await v.is_health():
                return v
        return None

    async def forward_request(
        self,
        request: Request,
        session_id: SessionId,
        on_complete: Callable[[str, Exception | None], Awaitable[None]] | None,
    ) -> Response:
        vpc_session = await self.get_avaliable_session(session_id)
        assert vpc_session, f"Vpc session not found! session_id={session_id}"
        return await self._forward_request(request, vpc_session, on_complete)

    async def _forward_request(
        self,
        request: Request,
        vpc_session: VpcSession,
        on_complete: Callable[[str, Exception | None], Awaitable[None]] | None,
    ) -> Response:
        """Forward the request through the persistent connection"""
        try:
            headers = dict(request.headers)

            # Remove headers that should not be forwarded or can cause conflicts
            for header in [
                "host",
                "connection",
                "upgrade",
            ]:
                headers.pop(header, None)

            # Forward request through connection
            backend_response = await vpc_session.forward_request(
                method=request.method,
                headers=headers,
                content=await request.body(),
                on_complete=on_complete,
            )
            return backend_response

        except BaseException as e:
            logger.error(
                f"Unexpected error forwarding request: {traceback.format_exc()}"
            )
            raise e

    async def handle_initialize_request(self, request: Request) -> Response:
        # Handle session init
        logger.info(
            f"MCP client session initialize request: remote.addr={get_remote_addr(request)}, request.headers={request.headers}"
        )
        time_start = time.time()

        session_id = SessionId.from_request(request)

        vpc_session = None
        if debug_mode:
            vpc_session = VpcSession(
                container_server_id="http://mcp-server-debug:8080",
                container_ip_addr="mcp-server-debug",
                image_version="mcp-server-debug",
                mcp_port=8080,
                novnc_port=5901,
                channel_port=8765,
                http_port=8000,
                container_id="mcp-server-debug",
                created_at=datetime.datetime.now(),
                last_active_at=datetime.datetime.now(),
            )
            logger.info(
                f"Gateway debug mode, skip container creation, default mcp_server connection={vpc_session}"
            )
        else:
            vpc_session = await self._init_vpc_session(session_id, request)

        # Forward request through the new persistent connection
        response = await self._forward_request(request, vpc_session, on_complete=None)
        mcp_session_id = response.headers.get("Mcp-Session-Id")
        assert (
            mcp_session_id
        ), "Mcp session id is required in initialize response header!"

        session_id.mcp_session_id = mcp_session_id

        # Bind session to VPC
        if (
            session_id.mcp_session_id
            and session_id.mcp_session_id not in vpc_session.mcp_session_ids
        ):
            vpc_session.mcp_session_ids.append(session_id.mcp_session_id)
        if (
            session_id.session_id
            and session_id.session_id not in vpc_session.session_ids
        ):
            vpc_session.session_ids.append(session_id.session_id)
        await self._session_repo.update_vpc_session(vpc_session)

        logger.info(
            f"Client session initialize success: session_id={session_id}, vpc_session.container_id={vpc_session.container_id}, time_cost_sec={time.time() - time_start}"
        )
        return response

    async def handle_delete_request(
        self, request: Request, session_id: SessionId
    ) -> Response:
        """Handle session delete request"""

        async def on_complete(response_body: str, e: Exception | None):
            logger.info(
                f"McpClient session release request {'error' if e else 'success'}: {get_remote_addr(request)}, session_id={session_id}, response_body={response_body}, error={e}"
            )

        try:
            mcp_response = await self.forward_request(
                request, session_id, on_complete=on_complete
            )
        except:
            logger.error(f"McpClient session release error! {traceback.format_exc()}")

        await self.release_mcp_session(session_id, request)
        return mcp_response

    async def cleanup_expired_sessions(self):
        """
        Cleanup expired sessions:
        - Env Pod health check failed after 3 retries
        - Env Pod last activity time is more than 1 hours
        """
        sessions = await self.get_sessions()
        for session in sessions:
            try:
                health = await session.is_health()
                if health:
                    last_active = session.last_active_at
                    if not last_active:
                        last_active = await get_pod_last_activate(
                            session.container_ip_addr, session.mcp_port
                        )
                    if last_active:
                        if (
                            time.time() - last_active.timestamp()
                            > MAX_LAST_ACTIVE_TIME_SEC
                        ):
                            logger.warning(
                                f"Env pod last_active is expired, shutdown container! session={session}, env_info={last_active}"
                            )
                            await self._session_repo.remove_vpc_session(session)
                            try:
                                await self.container_server_manager.shutdown_container(
                                    container_server_id=session.container_server_id,
                                    container_id=session.container_id,
                                )
                            except:
                                logger.error(
                                    f"Error shutdown health container: session={session}, {traceback.format_exc()}"
                                )
                    else:
                        logger.warning(
                            f"Env pod last_active is not found, check create_at! session={session}"
                        )
                        if session.created_at:
                            if (
                                time.time() - session.created_at.timestamp()
                                > MAX_CREATED_AT_TIME_SEC
                            ):
                                logger.warning(
                                    f"Env pod created_at is expired, shutdown container! session={session}"
                                )
                                await self._session_repo.remove_vpc_session(session)
                                try:
                                    await self.container_server_manager.shutdown_container(
                                        container_server_id=session.container_server_id,
                                        container_id=session.container_id,
                                    )
                                except:
                                    logger.error(
                                        f"Error shutdown health container error: session={session}, {traceback.format_exc()}"
                                    )
                        else:
                            logger.warning(
                                f"Env pod created_at is not found! session={session}"
                            )
                else:

                    async def final_health_check(session: VpcSession):
                        """
                        Final health check for env instance.
                        """
                        logger.info(
                            f"Final health check for env pod: session={session}"
                        )
                        for _ in range(3):
                            health = await session.is_health()
                            if health:
                                break
                            await asyncio.sleep(1)
                        else:
                            logger.warning(
                                f"Final health check for env pod failed! session={session}, remove session now!"
                            )
                            await self._session_repo.remove_vpc_session(session)
                            try:
                                await self.container_server_manager.shutdown_container(
                                    container_server_id=session.container_server_id,
                                    container_id=session.container_id,
                                )
                            except:
                                logger.error(
                                    f"Error shutdown unhealth container: session={session}, {traceback.format_exc()}"
                                )

                    asyncio.create_task(final_health_check(session))
            except:
                logger.error(f"Error clean session: {traceback.format_exc()}")

    async def remove_container(self, container_id: str):
        """Remove container"""
        session = next(
            (v for v in await self.get_sessions() if v.container_id == container_id),
            None,
        )
        assert session, f"Vpc session not found! container_id={container_id}"
        await self._session_repo.remove_vpc_session(session)
        try:
            await self.container_server_manager.shutdown_container(
                container_server_id=session.container_server_id,
                container_id=session.container_id,
            )
        except:
            logger.error(
                f"Error shutdown unhealth container: session={session}, {traceback.format_exc()}"
            )

    async def _init_vpc_session(
        self, session_id: SessionId, request: Request
    ) -> VpcSession:
        """Initialize a new VPC session"""
        vpc_session = await self.get_avaliable_session(session_id)
        if not vpc_session or vpc_session.is_expired():
            image_version = request.headers.get("IMAGE_VERSION")
            image_env_str = request.headers.get("SANDBOX_ENV") or request.headers.get(
                "IMAGE_ENV"
            )
            try:
                image_env = json.loads(image_env_str) if image_env_str else {}
            except BaseException as e:
                logger.error(f"Error parsing image env: {traceback.format_exc()}")
                image_env = {}

            logger.info(
                f"Create new vpc session: session_id={session_id}, image_version={image_version}, image_env={image_env}"
            )

            (
                container_id,
                container_ip_addr,
                container_mcp_port,
                container_novnc_port,
                container_channel_port,
                container_http_port,
                container_server_id,
            ) = await self.container_server_manager.create_container(
                session_id,
                image_version,
                image_env,
                trace_id=request.headers.get("TRACE_ID", "NA"),
            )

            vpc_session = VpcSession(
                container_id=container_id,
                container_ip_addr=container_ip_addr,
                image_version=image_version,
                mcp_port=container_mcp_port,
                novnc_port=container_novnc_port,
                channel_port=container_channel_port,
                http_port=container_http_port,
                container_server_id=container_server_id,
                created_at=datetime.datetime.now(),
                last_active_at=datetime.datetime.now(),
            )

        return vpc_session

    async def release_mcp_session(self, session_id: SessionId, request: Request):
        """Release the mcp session"""
        vpc_session = await self.get_avaliable_session(session_id)
        if not vpc_session:
            logger.error(f"Mcp session not found! session_id={session_id}")
            return

        assert session_id.mcp_session_id, "Mcp session id is required"
        assert vpc_session.is_bind(session_id), "Mcp session id not bind to vpc_session"
        if session_id.mcp_session_id in vpc_session.mcp_session_ids:
            vpc_session.mcp_session_ids.remove(session_id.mcp_session_id)

        if session_id.session_id:
            # Release Env for all mcp
            env_mode = request.headers.get("ENV_MODE")
            if "QUERY_INSTANT" == env_mode and not vpc_session.mcp_session_ids:
                await self._session_repo.remove_vpc_session(vpc_session)
                asyncio.create_task(
                    self.container_server_manager.shutdown_container(
                        container_server_id=vpc_session.container_server_id,
                        container_id=vpc_session.container_id,
                    )
                )
            # Release MCP Session only, keep env for multi-turn session
            else:
                await self._session_repo.update_vpc_session(vpc_session)
        else:
            await self._session_repo.remove_vpc_session(vpc_session)
            asyncio.create_task(
                self.container_server_manager.shutdown_container(
                    container_server_id=vpc_session.container_server_id,
                    container_id=vpc_session.container_id,
                    trace_id=request.headers.get("TRACE_ID", "NA"),
                )
            )

    async def get_sessions(self) -> List[VpcSession]:
        """Get all sessions"""
        return await self._session_repo.get_sessions()

    async def shutdown(self):
        """Shutdown the session connection manager"""
        pass


async def session_connection_manager_builder(
    container_server_manager: ContainerServerManager,
):
    if redis_url:
        session_repo = SessionRedisRepo(redis_url)
        await session_repo.initialize()
    else:
        session_repo = SessionRepo()

    return SessionConnectionManager(container_server_manager, session_repo)
