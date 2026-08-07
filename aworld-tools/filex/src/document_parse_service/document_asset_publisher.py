"""
文档资源发布抽象。

用于将本地资源上传到远端存储并回填 remote_id。
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import TYPE_CHECKING, Protocol

from .document_artifact_models import DocumentAsset

if TYPE_CHECKING:
    from services.afts_service import AftsService


logger = logging.getLogger(__name__)


class DocumentAssetPublisher(Protocol):
    """文档资源发布协议。"""

    async def publish_assets(self, assets: list[DocumentAsset]) -> list[DocumentAsset]:
        """发布资源并返回带 remote_id 的资源列表。"""


class NoOpDocumentAssetPublisher:
    """空实现，后续再替换为 AFTS 发布实现。"""

    async def publish_assets(self, assets: list[DocumentAsset]) -> list[DocumentAsset]:
        return assets


class AftsDocumentAssetPublisher:
    """基于 AFTS 的文档资源发布实现。"""

    def __init__(self, afts_service: "AftsService") -> None:
        self._afts_service = afts_service

    async def publish_assets(self, assets: list[DocumentAsset]) -> list[DocumentAsset]:
        published_assets: list[DocumentAsset] = []
        for asset in assets:
            if asset.remote_id:
                published_assets.append(asset)
                continue
            if asset.local_path is None:
                logger.warning(
                    "document_asset_publisher skip asset without local_path | asset_id=%s kind=%s",
                    asset.asset_id,
                    asset.kind,
                )
                continue
            if not asset.local_path.exists():
                logger.warning(
                    "document_asset_publisher asset file missing | asset_id=%s local_path=%s",
                    asset.asset_id,
                    asset.local_path,
                )
                continue

            try:
                remote_id = await self._afts_service.upload_file(
                    file_path=asset.local_path,
                    file_name=asset.local_path.name,
                    setpublic=True,
                    update_alias=True,
                )
            except BaseException as exc:
                logger.warning(
                    "document_asset_publisher upload failed | asset_id=%s local_path=%s error=%s",
                    asset.asset_id,
                    asset.local_path,
                    exc,
                    exc_info=True,
                )
                published_assets.append(asset)
                continue

            published_asset = replace(asset, remote_id=remote_id)
            remote_url = ""
            get_file_url = getattr(self._afts_service, "get_file_url", None)
            if callable(get_file_url):
                try:
                    remote_url = await get_file_url(remote_id) or ""
                except BaseException as exc:
                    logger.warning(
                        "document_asset_publisher get file url failed | asset_id=%s remote_id=%s error=%s",
                        asset.asset_id,
                        remote_id,
                        exc,
                        exc_info=True,
                    )
            if remote_url:
                published_asset.meta["remote_url"] = remote_url
                published_asset.meta["markdown_path"] = remote_url
            published_assets.append(published_asset)
        return published_assets
