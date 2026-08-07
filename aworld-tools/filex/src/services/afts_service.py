"""
AFTS文件存储服务

统一处理文件上传、下载等操作
"""

import hashlib
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Optional, Dict, Any

import aiohttp
from afts import Afts, DEFAULT_ENDPOINTS

logger = logging.getLogger(__name__)


def _download_cache_path(download_dir: Path, *, file_id: str, file_name: str) -> Path:
    """Build a stable, traversal-safe cache path scoped by immutable file id."""

    safe_name = Path(str(file_name or "")).name.strip() or "downloaded_file"
    parsed_name = Path(safe_name)
    stem = parsed_name.stem or "downloaded_file"
    suffix = parsed_name.suffix
    identity = hashlib.sha256(str(file_id).encode("utf-8")).hexdigest()[:8]
    return download_dir / f"{stem}-{identity}{suffix}"


def _atomic_write_bytes(output_path: Path, content: bytes) -> None:
    """Replace a shared cached download atomically after the full body is available."""

    temporary_path = output_path.with_name(
        f".{output_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temporary_path.write_bytes(content)
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)


class AftsService:
    """AFTS文件存储服务类"""

    def __init__(
        self,
        biz_key: str,
        biz_secret: str,
        app_id: Optional[str] = None,
        base_url: Optional[str] = None
    ):
        """初始化AFTS服务

        参数：
            biz_key: 业务标识
            biz_secret: 业务密钥
            app_id: 应用ID（可选，用于获取文件元信息）
            base_url: API基础URL（可选，默认使用 https://mmtcapi.alipay.com/meta/1.0/query）

        异常：
            ValueError: 当biz_key或biz_secret为空时
        """
        if not biz_key or not biz_key.strip():
            raise ValueError("biz_key是必填的，不能为空")
        if not biz_secret or not biz_secret.strip():
            raise ValueError("biz_secret是必填的，不能为空")
        if not app_id or not app_id.strip():
            raise ValueError("app_id是必填的，不能为空")


        endpoint_config = DEFAULT_ENDPOINTS.copy()
        # endpoint_config={
        #     "upload_endpoint": "mass.stable.alipay.net",
        #     "download_endpoint": "mass.stable.alipay.net",
        #     "authority_endpoint": "mmtcapi.stable.alipay.net"
        # }
        self.afts = Afts(
            biz_key=biz_key,
            biz_secret=biz_secret,
            endpoint_config=endpoint_config
        )
        self._biz_key = biz_key
        self._biz_secret = biz_secret
        self._app_id = app_id or "null"  # 默认为 "null" 字符串
        self._base_url = base_url or "https://mmtcapi.alipay.com"  # 默认API URL

    async def upload_file(
        self,
        file_path: Path,
        file_name: Optional[str] = None,
        setpublic: bool = True,
        update_alias: bool = True,
        alias: Optional[str] = None
    ) -> str:
        """上传文件到远程存储

        参数：
            file_path: 本地文件路径
            file_name: 文件名（如果为None，则从file_path中提取）
            setpublic: 是否公开，默认True
            update_alias: 是否更新已存在的别名，默认True
            alias: 文件别名（如果为None，则使用file_name）

        返回：
            文件ID

        异常：
            FileNotFoundError: 文件不存在
            RuntimeError: 上传失败
        """
        if not file_path.exists():
            logger.warning(
                f"afts_service.upload_file file not found | file_path={file_path}"
            )
            raise FileNotFoundError(f"File not found: {file_path}")

        if file_name is None:
            file_name = file_path.name

        # 如果提供了alias，使用alias；否则使用file_name
        alias_name = alias if alias is not None else file_name

        # 获取文件大小
        file_size = file_path.stat().st_size

        logger.info(
            f"afts_service.upload_file started | "
            f"file_path={file_path} file_name={file_name} file_size={file_size} "
            f"alias={alias_name} biz_key={self._biz_key} biz_secret_set={bool(self._biz_secret)} app_id={self._app_id}"
        )

        start_time = time.time()
        try:
            #file_path = str(file_path)
            file_id = self.afts.upload_file_by_path(
                file_path=str(file_path),
                file_name=file_name,
                setpublic=setpublic
            )
            duration = time.time() - start_time

            logger.info(
                f"afts_service.upload_file success | "
                f"file_id={file_id} file_name={file_name} alias={alias_name} duration={duration:.3f}s"
            )

            return file_id
        except BaseException as e:
            duration = time.time() - start_time
            logger.warning(
                f"afts_service.upload_file failed | "
                f"file_path={file_path} error={str(e)} duration={duration:.3f}s",
                exc_info=True
            )
            raise RuntimeError(f"Failed to upload file: {str(e)}") from e

    async def _get_acl_token(self, file_id: str, expire_time: int = 86400) -> str:
        """获取ACL Token

        参数：
            file_id: 文件ID
            expire_time: 过期时间（秒），默认1天，最长7天

        返回：
            Token字符串

        异常：
            RuntimeError: 获取Token失败
        """
        try:
            # 调用 afts.get_acl_token 方法，直接返回Token字符串
            token = self.afts.get_acl_token(
                file_id=file_id,
                expire_time=expire_time
            )
            return str(token)
        except BaseException as e:
            logger.warning(
                f"Failed to get ACL token for file {file_id}: {e}",
                exc_info=True
            )
            raise RuntimeError(f"获取ACL Token失败: {str(e)}") from e

    async def get_file_url(
        self,
        file_id: str,
        expire_time: int = 7*86400,
    ) -> str:
        """获取文件的直链 URL

        参数：
            file_id: 文件ID
            expire_time: URL 有效期（秒），默认 1 天，最长 7 天

        返回：
            可用于下载/访问文件的 URL 字符串；
            如果获取失败，返回空字符串（并记录日志）
        """
        logger.debug(
            f"afts_service.get_file_url called | "
            f"file_id={file_id} expire_time={expire_time}"
        )

        try:
            url = self.afts.get_url(
                file_id=file_id,
                expire_time=expire_time,
            )
            if not url:
                logger.warning(
                    f"afts_service.get_file_url failed, returned empty | file_id={file_id}"
                )
                return ""

            logger.debug(
                f"afts_service.get_file_url success | "
                f"file_id={file_id} url={url}"
            )
            return str(url)
        except BaseException as e:
            logger.warning(
                f"afts_service.get_file_url failed | "
                f"file_id={file_id} error={str(e)}",
                exc_info=True
            )
            # 按需求：不抛异常，返回空字符串
            return ""

    async def _get_file_meta(
        self,
        file_id: str,
        token: str,
        timestamp: str,
        app_id: Optional[str] = None,
        biz_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """获取文件元信息

        参数：
            file_id: 文件ID
            token: ACL Token
            timestamp: 时间戳
            app_id: 应用ID（可选，如果提供则覆盖实例的app_id）
            biz_type: 业务类型（可选，如果提供则覆盖实例的biz_key）

        返回：
            格式化的文件元信息字典，目前包含：
            - file_name: 文件名

        异常：
            RuntimeError: 获取文件元信息失败
        """
        try:
            # 使用传入的参数，如果没有则使用实例的默认值
            actual_app_id = app_id if app_id is not None else self._app_id
            actual_biz_type = biz_type if biz_type is not None else self._biz_key

            # 拼接URL：base_url + /meta/1.0/query
            base_url = self._base_url.rstrip('/')
            meta_url = f"{base_url}/meta/1.0/query"

            params = {
                "token": token,
                "timestamp": timestamp,
                "fileIds": file_id,
                "appId": actual_app_id,
                "bizType": actual_biz_type
            }

            logger.info(
                f"afts_service._get_file_meta request params | "
                f"meta_url={meta_url} file_id={file_id} app_id={actual_app_id} "
                f"biz_type={actual_biz_type} timestamp={timestamp}"
            )

            # 发起HTTP GET请求
            async with aiohttp.ClientSession() as session:
                async with session.get(meta_url, params=params) as response:
                    response.raise_for_status()
                    response_data = await response.json()

                    # 记录响应数据以便调试
                    logger.debug(f"File meta API response: {response_data}")

                    # 处理响应格式：可能是字典（包含data字段）或直接是列表
                    if isinstance(response_data, dict):
                        # 检查响应码
                        code = response_data.get("code", 0)
                        if code != 0:
                            msg = response_data.get("msg", f"错误代码: {code}")
                            logger.warning(f"获取文件元信息失败：{msg}")
                            raise RuntimeError(f"获取文件元信息失败：{msg}")

                        # 从data字段中获取文件列表
                        data_list = response_data.get("data", [])
                        if not isinstance(data_list, list) or len(data_list) == 0:
                            logger.warning(f"获取文件元信息失败：data字段为空或不是列表，响应: {response_data}")
                            raise RuntimeError("获取文件元信息失败：响应数据格式不正确（data字段为空）")

                        file_meta = data_list[0]
                    elif isinstance(response_data, list):
                        # 兼容旧格式：直接是列表
                        if len(response_data) == 0:
                            logger.warning(f"获取文件元信息失败：响应列表为空")
                            raise RuntimeError("获取文件元信息失败：响应格式不正确（列表为空）")

                        file_meta = response_data[0]

                        # 检查是否成功（有些API可能没有success字段，但有code字段）
                        if isinstance(file_meta, dict):
                            success = file_meta.get("success", True)  # 默认为True，如果没有success字段
                            code = file_meta.get("code", 0)
                            if not success and code != 0:
                                error_msg = file_meta.get("message", f"错误代码: {code}")
                                logger.warning(f"获取文件元信息失败：{error_msg}")
                                raise RuntimeError(f"获取文件元信息失败：{error_msg}")
                    else:
                        logger.warning(f"获取文件元信息失败：响应格式不正确，响应类型: {type(response_data)}, 内容: {response_data}")
                        raise RuntimeError(f"获取文件元信息失败：响应格式不正确（期望字典或列表，实际: {type(response_data).__name__}）")

                    # 确保file_meta是字典类型
                    if not isinstance(file_meta, dict):
                        logger.warning(f"获取文件元信息失败：file_meta不是字典类型，实际类型: {type(file_meta)}")
                        raise RuntimeError("获取文件元信息失败：响应数据格式不正确")

                    # 从返回结构中提取文件名
                    # 优先使用 ext.extmeta.name，如果没有则使用顶层的 name
                    ext = file_meta.get("ext", {})
                    extmeta = ext.get("extmeta", {}) if isinstance(ext, dict) else {}
                    file_name = extmeta.get("name") if extmeta else None
                    if not file_name:
                        file_name = file_meta.get("name", "")

                    # 如果还是没有文件名，使用file_id作为后备
                    if not file_name:
                        logger.warning(f"无法从元信息中提取文件名，使用file_id: {file_id}")
                        file_name = file_id

                    # 格式化返回数据（目前仅返回 file_name，后续可扩展）
                    result = {
                        "file_name": file_name
                    }

                    return result
        except BaseException as e:
            logger.warning(
                f"Failed to get file meta for {file_id}: {e}",
                exc_info=True
            )
            raise RuntimeError(f"获取文件元信息失败: {str(e)}") from e

    async def download_file(
        self,
        file_id: str,
        app_id: Optional[str] = None,
        biz_type: Optional[str] = None
    ) -> Path:
        """从远程存储下载文件

        参数：
            file_id: 文件ID
            app_id: 应用ID（可选，如果提供则覆盖实例的app_id）
            biz_type: 业务类型（可选，如果提供则覆盖实例的biz_key）

        返回：
            下载后的文件路径

        异常：
            RuntimeError: 下载失败
        """
        logger.info(
            f"afts_service.download_file started | "
            f"file_id={file_id} biz_key={self._biz_key} "
            f"biz_secret_set={bool(self._biz_secret)} app_id={self._app_id}"
        )

        start_time = time.time()

        # 确定临时下载目录
        download_dir = Path.home() / "workspace" / "tmp"
        download_dir.mkdir(parents=True, exist_ok=True)

        # 获取文件元信息以确定文件名
        try:
            # 1. 生成当前系统时间戳（毫秒），会与服务端时间校验不能超过10分钟
            timestamp = str(int(time.time() * 1000))

            # 2. 获取Token
            token = await self._get_acl_token(file_id, expire_time=86400)

            # 3. 获取文件元信息
            file_meta = await self._get_file_meta(
                file_id,
                token,
                timestamp,
                app_id=app_id,
                biz_type=biz_type
            )
            file_name = file_meta.get("file_name", file_id)

            # 4. 拼接完整路径
            output_path = _download_cache_path(download_dir, file_id=file_id, file_name=file_name)
        except BaseException as e:
            # 如果获取元信息失败，使用file_id作为文件名
            logger.warning(
                f"afts_service.download_file failed to get file meta, using file_id as filename | "
                f"file_id={file_id} error={str(e)}",
                exc_info=True
            )
            output_path = _download_cache_path(download_dir, file_id=file_id, file_name=file_id)

        try:
            # 获取文件URL（保留此逻辑）
            file_url = self.afts.get_url(
                file_id=file_id,
                expire_time=86400  # URL有效期（秒），默认1天，最长7天
            )
            if not file_url:
                raise RuntimeError("获取文件URL失败")

            # 确保输出目录存在
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # 使用 download_file 方法下载文件，并通过同目录临时文件原子更新稳定缓存路径。
            file_content = self.afts.download_file(file_id=file_id)
            if not file_content:
                error_msg = self.afts.err_msg() if hasattr(self.afts, 'err_msg') else "未知错误"
                raise RuntimeError(f"下载失败: {error_msg}")
            _atomic_write_bytes(output_path, file_content)

            # 验证下载的文件
            if not output_path.exists() or output_path.stat().st_size == 0:
                raise RuntimeError("文件下载失败或文件为空")

            file_size = output_path.stat().st_size
            duration = time.time() - start_time

            logger.info(
                f"afts_service.download_file success | "
                f"file_id={file_id} output_path={output_path} file_size={file_size} duration={duration:.3f}s"
            )

            return output_path
        except BaseException as e:
            duration = time.time() - start_time
            logger.warning(
                f"afts_service.download_file failed | "
                f"file_id={file_id} error={str(e)} duration={duration:.3f}s",
                exc_info=True
            )
            raise RuntimeError(f"Failed to download file: {str(e)}") from e

    @staticmethod
    def create_from_env_content(env_content: dict) -> "AftsService":
        """从环境上下文内容创建AFTS服务实例

        参数：
            env_content: 环境上下文内容字典，应包含 afts_biz_key 和 afts_biz_secret，可选 afts_app_id 和 afts_base_url

        返回：
            AftsService实例

        异常：
            ValueError: 缺少必要的参数
        """
        if not isinstance(env_content, dict):
            raise ValueError("env_content必须是字典类型")

        # 提取并处理参数（支持列表类型）
        afts_biz_key = env_content.get("afts_biz_key", "")
        afts_biz_secret = env_content.get("afts_biz_secret", "")
        afts_app_id = env_content.get("afts_app_id", "")
        afts_base_url = env_content.get("afts_base_url", "")


        return AftsService(
            biz_key=afts_biz_key,
            biz_secret=afts_biz_secret,
            app_id=afts_app_id,
            base_url=afts_base_url
        )
