"""
Markdown 组装抽象。

负责把文档资源按锚点插回 Markdown，或在无法定位时追加到附录区块。
"""

from __future__ import annotations

from html import escape
import re
from typing import Protocol

from .document_artifact_models import DocumentAsset, MarkdownArtifact


class MarkdownAssembler(Protocol):
    """Markdown 组装协议。"""

    def assemble(self, artifact: MarkdownArtifact) -> str:
        """根据正文与资源列表生成最终 Markdown。"""


class AnchoredMarkdownAssembler:
    """按锚点将图片 AFTS id 插回正文。"""

    def assemble(self, artifact: MarkdownArtifact) -> str:
        if not artifact.assets:
            return artifact.markdown_text

        updated_markdown = artifact.markdown_text.rstrip()
        unresolved_assets: list[DocumentAsset] = []

        sorted_assets = sorted(
            artifact.assets,
            key=lambda item: (
                item.page_number,
                item.anchor.top,
                item.anchor.left,
                item.order,
            ),
        )

        for asset in sorted_assets:
            asset_reference = self._resolve_asset_reference(asset)
            if not asset_reference:
                unresolved_assets.append(asset)
                continue

            image_markdown = self._build_image_markdown(asset)
            if self._has_asset_reference(updated_markdown, asset_reference):
                continue

            if str(asset.meta.get("placement", "")) == "append_only":
                unresolved_assets.append(asset)
                continue

            before_end = self._find_last_snippet_match_end(
                updated_markdown,
                asset.anchor.before_snippets,
            )
            after_start = self._find_first_snippet_match_start(
                updated_markdown,
                asset.anchor.after_snippets,
            )

            if before_end is not None and (after_start is None or before_end <= after_start):
                insert_at = self._line_end_position(updated_markdown, before_end)
                updated_markdown = self._insert_markdown_block(
                    updated_markdown,
                    insert_at=insert_at,
                    block=image_markdown,
                )
                continue

            if after_start is not None:
                insert_at = after_start
                if asset.anchor.before_snippets:
                    insert_at = self._line_start_position(updated_markdown, after_start)
                updated_markdown = self._insert_markdown_block(
                    updated_markdown,
                    insert_at=insert_at,
                    block=image_markdown,
                )
                continue

            unresolved_assets.append(asset)

        fallback_section = self._build_fallback_section(unresolved_assets)
        if fallback_section:
            updated_markdown = f"{updated_markdown}\n\n{fallback_section}"
        return updated_markdown

    def _build_fallback_section(self, assets: list[DocumentAsset]) -> str:
        lines = ["## 提取图片", ""]
        has_content = False
        for asset in assets:
            if not self._resolve_asset_reference(asset):
                continue
            lines.append(self._build_image_markdown(asset).strip())
            lines.append("")
            has_content = True
        return "\n".join(lines).strip() if has_content else ""

    def _build_image_markdown(self, asset: DocumentAsset) -> str:
        image_index = str(asset.meta.get("index", "") or asset.order or "")
        asset_reference = self._resolve_asset_reference(asset)
        image_alt = f"图片 {image_index}".strip()
        if asset.remote_id:
            return (
                f'\n\n<img src="{escape(asset_reference, quote=True)}" '
                f'data-file-id="{escape(asset.remote_id, quote=True)}" '
                f'alt="{escape(image_alt, quote=True)}" />\n\n'
            )
        return f"\n\n![{image_alt}]({asset_reference})\n\n"

    @staticmethod
    def _resolve_asset_reference(asset: DocumentAsset) -> str:
        return str(
            asset.meta.get("remote_url", "")
            or asset.meta.get("markdown_path", "")
            or asset.remote_id
            or asset.meta.get("local_path", "")
        ).strip()

    @staticmethod
    def _has_asset_reference(markdown_text: str, asset_reference: str) -> bool:
        return (
            f"]({asset_reference})" in markdown_text
            or f'src="{asset_reference}"' in markdown_text
            or f"src='{asset_reference}'" in markdown_text
        )

    def _find_last_snippet_match_end(self, markdown_text: str, snippets: list[str]) -> int | None:
        for snippet in snippets:
            pattern = self._compile_whitespace_tolerant_pattern(snippet)
            if pattern is None:
                continue
            matches = list(pattern.finditer(markdown_text))
            if matches:
                return matches[-1].end()
        return None

    def _find_first_snippet_match_start(self, markdown_text: str, snippets: list[str]) -> int | None:
        for snippet in snippets:
            pattern = self._compile_whitespace_tolerant_pattern(snippet)
            if pattern is None:
                continue
            match = pattern.search(markdown_text)
            if match:
                return match.start()
        return None

    @staticmethod
    def _compile_whitespace_tolerant_pattern(snippet: str) -> re.Pattern[str] | None:
        tokens = [token for token in snippet.split() if token]
        if len("".join(tokens)) < 8:
            return None
        return re.compile(r"\s+".join(re.escape(token) for token in tokens), re.IGNORECASE)

    @staticmethod
    def _insert_markdown_block(markdown_text: str, *, insert_at: int, block: str) -> str:
        before = markdown_text[:insert_at].rstrip("\n")
        after = markdown_text[insert_at:].lstrip("\n")
        return f"{before}{block}{after}"

    @staticmethod
    def _line_end_position(markdown_text: str, position: int) -> int:
        line_end = markdown_text.find("\n", position)
        if line_end == -1:
            return len(markdown_text)
        return line_end

    @staticmethod
    def _line_start_position(markdown_text: str, position: int) -> int:
        line_start = markdown_text.rfind("\n", 0, position)
        if line_start == -1:
            return 0
        return line_start + 1


class PassthroughMarkdownAssembler:
    """保留 passthrough 骨架，供未接入资源组装的场景使用。"""

    def assemble(self, artifact: MarkdownArtifact) -> str:
        return artifact.markdown_text


class PlaceholderMarkdownAssembler:
    """将正文中的资源占位符替换为图片 Markdown。"""

    def assemble(self, artifact: MarkdownArtifact) -> str:
        if not artifact.assets:
            return artifact.markdown_text

        updated_markdown = artifact.markdown_text.rstrip()
        unresolved_assets: list[DocumentAsset] = []

        for asset in sorted(artifact.assets, key=lambda item: (item.order, item.asset_id)):
            if not self._resolve_asset_reference(asset):
                unresolved_assets.append(asset)
                continue

            placeholder = self._build_placeholder(asset.asset_id)
            image_markdown = self._build_image_markdown(asset)

            if placeholder in updated_markdown:
                updated_markdown = updated_markdown.replace(placeholder, image_markdown)
                continue

            unresolved_assets.append(asset)

        fallback_section = self._build_fallback_section(unresolved_assets)
        if fallback_section:
            updated_markdown = f"{updated_markdown}\n\n{fallback_section}"
        return updated_markdown

    @staticmethod
    def _build_placeholder(asset_id: str) -> str:
        return f"{{{{asset:{asset_id}}}}}"

    def _build_image_markdown(self, asset: DocumentAsset) -> str:
        image_index = str(asset.meta.get("index", "") or asset.order or "")
        asset_reference = self._resolve_asset_reference(asset)
        image_alt = f"图片 {image_index}".strip()
        if asset.remote_id:
            return (
                f'<img src="{escape(asset_reference, quote=True)}" '
                f'data-file-id="{escape(asset.remote_id, quote=True)}" '
                f'alt="{escape(image_alt, quote=True)}" />'
            )
        return f"![{image_alt}]({asset_reference})"

    def _build_fallback_section(self, assets: list[DocumentAsset]) -> str:
        lines = ["## 提取图片", ""]
        has_content = False
        for asset in assets:
            if not self._resolve_asset_reference(asset):
                continue
            lines.append(self._build_image_markdown(asset))
            lines.append("")
            has_content = True
        return "\n".join(lines).strip() if has_content else ""

    @staticmethod
    def _resolve_asset_reference(asset: DocumentAsset) -> str:
        return str(
            asset.meta.get("remote_url", "")
            or asset.meta.get("markdown_path", "")
            or asset.remote_id
            or asset.meta.get("local_path", "")
        ).strip()
