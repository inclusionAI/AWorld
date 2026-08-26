from functools import cache
from pathlib import Path
import re
import socket
import logging
import traceback
import asyncio
import uuid
import httpx
import threading
from datetime import datetime

from .dockers import docker_helper

from .configs import (
    container_server_port,
    docker_registry_url,
    docker_registry_user_name,
    docker_registry_password,
    default_mcp_server_image_version,
    mcp_server_image_name,
    gateway_server_addr,
    docker_mode,
    debug_mode,
    mcp_container_mem_limit,
)

_MEM_LIMIT_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*([gGmMkK]?)\s*$",
)


def parse_mem_limit_to_bytes(s: str) -> int:
    """Parse Docker-style mem limit (e.g. 8G, 512M, 128K) to bytes (powers of 1024)."""
    raw = s.strip()
    if not raw:
        raise ValueError("mem_limit string is empty")
    m = _MEM_LIMIT_RE.match(raw)
    if not m:
        raise ValueError(f"invalid mem_limit: {raw!r}")
    val = float(m.group(1))
    unit = (m.group(2) or "").upper()
    mult = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3}
    if unit not in mult:
        raise ValueError(f"invalid mem_limit unit in {raw!r}")
    return int(val * mult[unit])


def read_host_memory_kib() -> tuple[int, int]:
    """Return (MemTotal_kib, MemAvailable_or_MemFree_kib) from /proc/meminfo."""
    mem_total: int | None = None
    mem_available: int | None = None
    mem_free: int | None = None
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_total = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    mem_available = int(line.split()[1])
                elif line.startswith("MemFree:"):
                    mem_free = int(line.split()[1])
    except OSError as e:
        raise ValueError(f"cannot read host memory: {e}") from e

    if mem_total is None:
        raise ValueError("MemTotal not found in /proc/meminfo")

    if mem_available is not None:
        return mem_total, mem_available

    if mem_free is not None:
        logger.warning("MemAvailable missing in /proc/meminfo, using MemFree for memory gate")
        return mem_total, mem_free

    raise ValueError("Neither MemAvailable nor MemFree in /proc/meminfo")


def assert_memory_allows_new_container(mem_limit_str: str) -> None:
    total_kb, avail_kb = read_host_memory_kib()
    if total_kb <= 0:
        raise ValueError("invalid MemTotal from host")
    avail_ratio = avail_kb / total_kb
    if avail_ratio < 0.1:
        raise ValueError(
            f"insufficient host memory: available {avail_ratio * 100:.1f}% of total, "
            "need at least 10% available"
        )
    requested_b = parse_mem_limit_to_bytes(mem_limit_str)
    avail_b = avail_kb * 1024
    if requested_b > avail_b:
        raise ValueError(
            f"requested container memory {mem_limit_str} ({requested_b} bytes) exceeds "
            f"host available memory (~{avail_b} bytes)"
        )

logger = logging.getLogger(__name__)


token = str(uuid.uuid4())


async def wait_docker_ready(timeout: int = 30):
    for i in range(timeout):
        try:
            cmd = ["docker", "ps"]
            p = await asyncio.subprocess.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
            )
            stdout, _ = await p.communicate()
            if p.returncode == 0:
                logger.info(f"Docker daemon is ready! \n{stdout.decode()}")
                return
            else:
                logger.warning(f"Docker daemon is not ready! {stdout.decode()}")
        except:
            logger.error(f"Docker daemon is not ready! {traceback.format_exc()}")
        await asyncio.sleep(1)
    else:
        logger.error(f"Docker daemon is not ready after {timeout} seconds!")
        raise Exception(f"Docker daemon is not ready after {timeout} seconds!")


async def ensure_docker_env():
    try:
        t, u, f = docker_helper.get_disk_space()
        to_gb = lambda i: i / (1024 * 1024 * 1024)
        logger.info(
            f"Host disk space: total={to_gb(t)}, used={to_gb(u)}, free={to_gb(f)}"
        )
        if u / t > 0.8:
            logger.warning(
                f"Host disk space used more than 80%, cleaning docker resources"
            )
            await docker_helper.prune_async()
    except BaseException as e:
        logger.error(f"Ensure docker env failed: {traceback.format_exc()}")
        raise


async def start_container_server_register_task():
    register_url = f"{gateway_server_addr}/api/container_server/register"
    local_ip_addr = get_local_ip()

    async def register():
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    register_url,
                    json={
                        "token": token,
                        "ip_addr": local_ip_addr,
                        "port": container_server_port,
                        "cpu_load": [],
                        "memory_usage": [],
                    },
                    timeout=10.0,
                )

            response.raise_for_status()

        except BaseException as e:
            logger.error(f"Register container server failed: {register_url}, {e}")
            raise

    async def init_register():
        for _ in range(20):
            try:
                await register()
                logger.info(f"Init register success")
                break
            except:
                await asyncio.sleep(3)
        else:
            logger.error(f"Init register failed after 20 times!")
            raise Exception(f"Init register failed after 20 times!")

    await init_register()

    async def update_register():
        while True:
            try:
                await register()
            except:
                logger.error(f"Update register failed: {traceback.format_exc()}")
            await asyncio.sleep(10)

    asyncio.create_task(update_register())


async def load_mcp_server_image():
    if docker_registry_url and docker_registry_user_name and docker_registry_password:
        await docker_helper.login_async(
            registry_url=docker_registry_url,
            username=docker_registry_user_name,
            password=docker_registry_password,
        )
    if not debug_mode:
        for i in range(5):
            try:
                if mcp_server_image_name:
                    default_image_id = (
                        f"{mcp_server_image_name}:{default_mcp_server_image_version}"
                    )
                    await docker_helper.pull_async(default_image_id)
                    break
                else:
                    raise Exception(f"Env MCP_SERVER_IMAGE_NAME is not set!")
            except BaseException as e:
                logger.error(f"Pull mcp server image failed: {traceback.format_exc()}")

                await asyncio.sleep(2 * (i + 1))
        else:
            logger.error(f"Pull mcp server image failed after 5 times!")
            raise Exception(f"Pull mcp server image failed after 5 times!")


async def start_mcp_server_life_cycle_manager():
    pass


async def clean_mcp_server_container():
    pass


async def create_mcp_server_container(
    session_id: str | None,
    image_version: str | None,
    image_env: dict[str, str] | None,
):
    assert_memory_allows_new_container(mcp_container_mem_limit)

    mcp_port = docker_helper.get_available_port()
    novnc_port = docker_helper.get_available_port()
    channel_port = docker_helper.get_available_port()
    http_port = docker_helper.get_available_port()
    ip_addr = get_local_ip()

    image_id = (
        f"{mcp_server_image_name}:{image_version or default_mcp_server_image_version}"
    )

    cur_timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    container_name = f"mcp_server_{cur_timestamp}_{uuid.uuid4().hex}"
    try:
        ports = {
            8080: f"{mcp_port}",
            5901: f"{novnc_port}",
            8765: f"{channel_port}",
            8000: f"{http_port}",
        }

        image_env = image_env or {}

        volumes = {"/etc/resolv.conf": {"bind": "/etc/resolv.conf", "mode": "ro"}}

        def _has_custome_workspace_params(image_env: dict[str, str]) -> bool:
            required_keys = {
                "WORKSPACE_PATH",
                "OSS_WORKSPACE_ENDPOINT",
                "OSS_WORKSPACE_BUCKET",
                "OSS_WORKSPACE_PATH",
            }
            return required_keys.issubset(image_env.keys())

        if session_id and not _has_custome_workspace_params(image_env):
            host_path = f"/mnt/ossfs/mcp-server/workspace/{session_id}"
            Path(host_path).mkdir(parents=True, exist_ok=True)
            container_path = "/root/workspace/share"
            Path(container_path).mkdir(parents=True, exist_ok=True)
            volumes[host_path] = {"bind": container_path, "mode": "rw"}
            image_env["SESSION_ID"] = session_id
            logger.info(f"Use default workspace volumes: {volumes}")

        logger.info(
            f"Create mcp server container: {container_name}, image_id: {image_id}, mcp_port: {mcp_port}, novnc_port: {novnc_port}"
        )

        container = await docker_helper.run_async(
            image_id=image_id,
            container_name=container_name,
            ports=ports,
            volumes=volumes,
            environments=image_env,
            mem_limit=mcp_container_mem_limit,
            privileged=True,
        )

        logger.info(
            f"Create mcp server container success, waiting for Ready: {container.id}, ip_addr: {ip_addr}, mcp_port: {mcp_port}, novnc_port: {novnc_port}"
        )

        def tail_logs():
            logs = container.logs(stream=True, tail=100, follow=True)
            try:
                buffer = []
                for line in logs:
                    buffer.append(line.decode())
                    if len(buffer) >= 20:
                        logger.info(
                            f"VPC[{container.name}] >>>\n|-> {'|-> '.join(buffer)}\n"
                        )
                        buffer.clear()
                if buffer:
                    logger.info(
                        f"VPC[{container.name}] >>> \n|-> {'|-> '.join(buffer)}\n"
                    )
                    buffer.clear()
                logger.info(f"VPC [{container.name}] logs end!")
            except BaseException as e:
                logger.error(f"Error in tail_logs: {e}")

        # Start log tailing in background thread
        log_thread = threading.Thread(
            target=tail_logs, name=f"VPC_{container.name}_logs", daemon=True
        )
        log_thread.start()

        async def health_check(timeout: float = 3.0):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"http://{ip_addr}:{mcp_port}/health",
                        timeout=httpx.Timeout(timeout),
                    )
                    response.raise_for_status()
                    return True
            except BaseException as e:
                logger.error(f"Check mcp server health error! {e}")
                return False

        max_check = 120
        check_interval = 1
        for i in range(max_check):
            if await health_check():
                logger.info(
                    f"MCP server {ip_addr}:{mcp_port} is ready: {i+1}/{max_check}"
                )
                break
            else:
                logger.warning(
                    f"MCP server {ip_addr}:{mcp_port} is not ready: {i+1}/{max_check}"
                )
                await asyncio.sleep(check_interval)
        else:
            logger.error(
                f"MCP server {ip_addr}:{mcp_port} is not ready after {max_check} times!"
            )
            raise Exception(
                f"MCP server {ip_addr}:{mcp_port} is not ready after {max_check} times!"
            )

        if docker_mode == "host":
            ip_addr = await docker_helper.get_container_ip(container.id)
            mcp_port = 8080
            novnc_port = 5901
            channel_port = 8765
            http_port = 8000

        return container.name, ip_addr, mcp_port, novnc_port, channel_port, http_port
    except:
        logger.error(f"Create mcp server container failed: {traceback.format_exc()}")
        raise


async def shutdown_mcp_server_container(container_id: str):
    try:
        await docker_helper.stop_async(container_id)
    except:
        logger.error(f"Shutdown mcp server container failed: {traceback.format_exc()}")
        raise


async def list_containers():
    try:
        return await docker_helper.list_containers_async()
    except:
        logger.error(f"List containers failed: {traceback.format_exc()}")
        raise


async def wait_oss_ready():
    try:
        mount_path = "/mnt/ossfs"
        Path(mount_path).mkdir(parents=True, exist_ok=True)
        p = await asyncio.subprocess.create_subprocess_exec(
            "mount",
            "-t",
            "nfs",
            "-o",
            "nolock,proto=tcp,rsize=1048576,wsize=1048576,hard,timeo=600,retrans=2,noresvport",
            "10.1.231.101:/aworld_oss_public",
            "/mnt/ossfs",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await p.communicate()
        if p.returncode == 0:
            logger.info(f"OSS mount is ready! \n{stdout.decode()}")
            return
        else:
            logger.error(f"OSS mount is not ready! {stdout.decode()}")
    except:
        logger.error(f"Wait OSS mount ready failed: {traceback.format_exc()}")


@cache
def get_local_ip() -> str | None:
    try:
        host_name = socket.gethostname()
        _, _, ip_list = socket.gethostbyname_ex(host_name)
        for ip in ip_list:
            if not ip.startswith("127."):
                return ip
    except BaseException as e:
        logger.error(f"Get local ip failed: {traceback.format_exc()}")

    raise RuntimeError("Get local ip failed")
