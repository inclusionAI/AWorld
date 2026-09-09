from __future__ import annotations

import os
import stat
from dataclasses import replace
from pathlib import Path
from typing import Callable, Iterable

from .extractors import (
    BINARY_MEDIA_TYPE,
    builtin_extractors,
    detect_media_type,
    extractor_for,
    fingerprint_regular_file,
)
from .types import (
    DatasetExtractor,
    IngestionContractError,
    IngestionDiagnostic,
    IngestionLimits,
    SourceAsset,
    SourceInventory,
    SourceKind,
)


_EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".aworld",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        "node_modules",
        "build",
        "dist",
        "cache",
        ".cache",
        ".pytest_cache",
        ".mypy_cache",
    }
)
_SECRET_FILE_NAMES = frozenset(
    {
        ".env",
        ".netrc",
        "credentials",
        "credentials.json",
        "id_rsa",
        "id_ed25519",
        "aworld-source.yaml",
        "aworld-source.yml",
    }
)


class SourceScanError(IngestionContractError):
    pass


class SourceScanner:
    """Deterministically inventories one local file or root-bounded directory."""

    def __init__(
        self,
        *,
        limits: IngestionLimits | None = None,
        extractors: Iterable[DatasetExtractor] | None = None,
    ) -> None:
        self.limits = limits or IngestionLimits()
        self.extractors = tuple(extractors or builtin_extractors())

    def scan(
        self,
        source_path: str | Path,
        *,
        before_recheck: Callable[[], None] | None = None,
    ) -> SourceInventory:
        supplied = Path(source_path).expanduser()
        if not supplied.exists():
            raise SourceScanError("source_not_found", "source path does not exist")
        if supplied.is_symlink():
            raise SourceScanError(
                "source_symlink_not_allowed",
                "explicit source path cannot be a symlink",
            )
        if not os.access(supplied, os.R_OK):
            raise SourceScanError("source_not_readable", "source path is not readable")

        absolute = supplied.absolute()
        if absolute.is_file():
            source_kind = SourceKind.FILE
            root = absolute.parent
            discovered = [(absolute.name, absolute)]
            ignored: list[IngestionDiagnostic] = []
        elif absolute.is_dir():
            source_kind = SourceKind.DIRECTORY
            root = absolute
            discovered, ignored = self._discover_directory(root)
        else:
            raise SourceScanError(
                "source_not_regular",
                "source must be a regular file or directory",
            )

        if len(discovered) > self.limits.max_files:
            raise SourceScanError(
                "source_limit_exceeded",
                "source exceeds max_files",
            )
        total_bytes = 0
        for _, path in discovered:
            try:
                metadata = path.stat(follow_symlinks=False)
            except OSError as exc:
                raise SourceScanError(
                    "source_changed_during_ingestion",
                    "source asset disappeared during inventory",
                ) from exc
            if not stat.S_ISREG(metadata.st_mode):
                raise SourceScanError(
                    "source_changed_during_ingestion",
                    "source asset is no longer a regular file",
                )
            total_bytes += metadata.st_size
        if total_bytes > self.limits.max_total_bytes:
            raise SourceScanError(
                "source_limit_exceeded",
                "source exceeds max_total_bytes",
            )

        assets: list[SourceAsset] = []
        rejected: list[IngestionDiagnostic] = []
        initial_fingerprints: dict[str, str] = {}
        for relative_path, path in discovered:
            asset, diagnostic = self._scan_asset(
                relative_path,
                path,
                root=root,
            )
            assets.append(asset)
            initial_fingerprints[relative_path] = asset.content_fingerprint
            if diagnostic is not None:
                rejected.append(diagnostic)

        if before_recheck is not None:
            before_recheck()
        self._verify_unchanged(
            root,
            initial_fingerprints,
            source_kind=source_kind,
        )
        return SourceInventory.create(
            source_kind=source_kind,
            assets=assets,
            ignored_assets=ignored,
            rejected_assets=rejected,
        )

    def _discover_directory(
        self,
        root: Path,
    ) -> tuple[list[tuple[str, Path]], list[IngestionDiagnostic]]:
        discovered: list[tuple[str, Path]] = []
        ignored: list[IngestionDiagnostic] = []
        for current, directory_names, file_names in os.walk(
            root,
            topdown=True,
            followlinks=False,
        ):
            current_path = Path(current)
            kept_directories: list[str] = []
            for name in sorted(directory_names):
                path = current_path / name
                relative = path.relative_to(root).as_posix()
                if path.is_symlink():
                    ignored.append(
                        IngestionDiagnostic(
                            reason_code="internal_symlink_ignored",
                            record_locator=relative,
                        )
                    )
                    continue
                if self._excluded(relative, is_directory=True):
                    ignored.append(
                        IngestionDiagnostic(
                            reason_code="default_excluded",
                            record_locator=relative,
                        )
                    )
                    continue
                kept_directories.append(name)
            directory_names[:] = kept_directories
            for name in sorted(file_names):
                path = current_path / name
                relative = path.relative_to(root).as_posix()
                if path.is_symlink():
                    ignored.append(
                        IngestionDiagnostic(
                            reason_code="internal_symlink_ignored",
                            record_locator=relative,
                        )
                    )
                    continue
                if self._excluded(relative, is_directory=False):
                    ignored.append(
                        IngestionDiagnostic(
                            reason_code="default_excluded",
                            record_locator=relative,
                        )
                    )
                    continue
                if not path.is_file():
                    ignored.append(
                        IngestionDiagnostic(
                            reason_code="non_regular_asset_ignored",
                            record_locator=relative,
                        )
                    )
                    continue
                discovered.append((relative, path))
        discovered.sort(key=lambda item: item[0])
        return discovered, ignored

    def _excluded(self, relative_path: str, *, is_directory: bool) -> bool:
        parts = Path(relative_path).parts
        name = parts[-1]
        if any(part.startswith(".") for part in parts):
            return True
        if is_directory and name in _EXCLUDED_DIRECTORY_NAMES:
            return True
        return not is_directory and name.lower() in _SECRET_FILE_NAMES

    def _scan_asset(
        self,
        relative_path: str,
        path: Path,
        *,
        root: Path,
    ) -> tuple[SourceAsset, IngestionDiagnostic | None]:
        try:
            metadata = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode):
                raise IngestionContractError(
                    "source_changed_during_ingestion",
                    "source asset is no longer a regular file",
                )
            exceeds_file_limit = (
                metadata.st_size > self.limits.max_file_bytes
            )
            content_fingerprint, size_bytes, sample = fingerprint_regular_file(
                path,
                max_bytes=self.limits.max_total_bytes,
                sample_bytes=self.limits.max_asset_sample_bytes,
                source_root=root,
                relative_path=relative_path,
            )
            if size_bytes != metadata.st_size:
                raise IngestionContractError(
                    "source_changed_during_ingestion",
                    "source asset size changed before fingerprinting",
                )
        except IngestionContractError as exc:
            raise SourceScanError(
                exc.reason_code,
                "source asset could not be read consistently",
            ) from exc
        media_type = detect_media_type(path, sample)
        provisional = SourceAsset(
            asset_id=SourceAsset.identity_for(relative_path, content_fingerprint),
            relative_path=relative_path,
            media_type=media_type,
            size_bytes=size_bytes,
            content_fingerprint=content_fingerprint,
        )
        extractor = extractor_for(provisional, extractors=self.extractors)
        diagnostic: IngestionDiagnostic | None = None
        if exceeds_file_limit:
            diagnostic = IngestionDiagnostic(
                reason_code="asset_size_limit_exceeded",
                asset_identity=provisional.asset_id,
                record_locator=relative_path,
            )
            return provisional, diagnostic
        if media_type == BINARY_MEDIA_TYPE or extractor is None:
            diagnostic = IngestionDiagnostic(
                reason_code="unsupported_media_type",
                asset_identity=provisional.asset_id,
                record_locator=relative_path,
                detail=f"media_type={media_type}",
            )
            return provisional, diagnostic
        try:
            document = extractor.extract(
                path,
                asset=provisional,
                limits=self.limits,
            )
        except IngestionContractError as exc:
            diagnostic = IngestionDiagnostic(
                reason_code=exc.reason_code,
                asset_identity=provisional.asset_id,
                record_locator=relative_path,
            )
            return provisional, diagnostic
        return (
            replace(
                provisional,
                extractor_name=extractor.name,
                extractor_version=extractor.version,
                structural_profile=document.structural_profile,
            ),
            None,
        )

    def _verify_unchanged(
        self,
        root: Path,
        expected: dict[str, str],
        *,
        source_kind: SourceKind,
    ) -> None:
        if source_kind == SourceKind.DIRECTORY:
            rediscovered, _ = self._discover_directory(root)
            if {relative for relative, _ in rediscovered} != set(expected):
                raise SourceScanError(
                    "source_changed_during_ingestion",
                    "source asset set changed after scan",
                )
        for relative_path, fingerprint in sorted(expected.items()):
            path = root / relative_path
            if path.is_symlink() or not path.is_file():
                raise SourceScanError(
                    "source_changed_during_ingestion",
                    "source asset changed after scan",
                )
            try:
                current, _, _ = fingerprint_regular_file(
                    path,
                    max_bytes=self.limits.max_total_bytes,
                    source_root=root,
                    relative_path=relative_path,
                )
            except IngestionContractError as exc:
                raise SourceScanError(
                    exc.reason_code,
                    "source asset changed after scan",
                ) from exc
            if current != fingerprint:
                raise SourceScanError(
                    "source_changed_during_ingestion",
                    "source asset content changed after scan",
                )


def scan_source(
    source_path: str | Path,
    *,
    limits: IngestionLimits | None = None,
    extractors: Iterable[DatasetExtractor] | None = None,
) -> SourceInventory:
    return SourceScanner(limits=limits, extractors=extractors).scan(source_path)
