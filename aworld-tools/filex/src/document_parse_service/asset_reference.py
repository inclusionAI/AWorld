"""Artifact-level asset reference normalization for Markdown output."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from .document_artifact_models import DocumentAsset


AssetReferenceMode = Literal["remote_id", "local_path"]


def prepare_markdown_asset_references(
    assets: list[DocumentAsset],
    *,
    output_dir: Path,
    asset_reference_mode: AssetReferenceMode,
) -> None:
    """Populate Markdown-facing asset references according to output policy."""
    if asset_reference_mode != "local_path":
        return

    for asset in assets:
        if asset.remote_id or asset.local_path is None:
            continue
        try:
            markdown_path = str(asset.local_path.relative_to(output_dir))
        except ValueError:
            markdown_path = str(asset.local_path)
        asset.meta["markdown_path"] = markdown_path
        asset.meta["local_path"] = str(asset.local_path)
