from __future__ import annotations

import re
from threading import RLock
from typing import Iterable

from .agent import AgenticDatasetIngestor
from .extractors import builtin_extractors
from .extractors import extractor_fingerprint
from .types import (
    DatasetExtractor,
    DatasetIngestor,
    FrozenIngestionSnapshot,
    IngestionContractError,
    IngestorTrustLevel,
    validate_fingerprint,
)


_REGISTERED_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class IngestionRegistry:
    """Explicit object registry; import strings and entry-point execution are absent."""

    def __init__(
        self,
        *,
        extractors: Iterable[DatasetExtractor] = (),
        ingestors: Iterable[DatasetIngestor] = (),
        allowlisted_ingestor_fingerprints: Iterable[str] = (),
        allowlisted_extractor_fingerprints: Iterable[str] = (),
    ) -> None:
        self._lock = RLock()
        self._extractors: dict[str, DatasetExtractor] = {}
        self._ingestors: dict[str, DatasetIngestor] = {}
        self._allowlisted_ingestor_fingerprints = frozenset(
            validate_fingerprint(
                value,
                field_name="allowlisted_ingestor_fingerprint",
            )
            for value in allowlisted_ingestor_fingerprints
        )
        self._allowlisted_extractor_fingerprints = frozenset(
            validate_fingerprint(
                value,
                field_name="allowlisted_extractor_fingerprint",
            )
            for value in allowlisted_extractor_fingerprints
        )
        for extractor in extractors:
            self.register_extractor(extractor)
        for ingestor in ingestors:
            self.register_ingestor(ingestor)

    def register_extractor(
        self,
        extractor: DatasetExtractor,
        *,
        replace: bool = False,
    ) -> None:
        name = _validate_name(getattr(extractor, "name", None), kind="extractor")
        version = getattr(extractor, "version", None)
        if not isinstance(version, str) or not version:
            raise IngestionContractError(
                "registry_entry_invalid",
                "extractor version must be non-empty",
            )
        if not callable(getattr(extractor, "supports", None)) or not callable(
            getattr(extractor, "extract", None)
        ):
            raise IngestionContractError(
                "registry_entry_invalid",
                "extractor does not implement the DatasetExtractor protocol",
            )
        trust_level = getattr(
            extractor,
            "trust_level",
            IngestorTrustLevel.EXTERNAL_UNTRUSTED,
        )
        if not isinstance(trust_level, IngestorTrustLevel):
            raise IngestionContractError(
                "registry_entry_invalid",
                "extractor trust_level must use IngestorTrustLevel",
            )
        if (
            trust_level is IngestorTrustLevel.FRAMEWORK_BUILTIN
            and not any(
                type(extractor) is type(builtin)
                and extractor.name == builtin.name
                and extractor.version == builtin.version
                for builtin in builtin_extractors()
            )
        ):
            raise IngestionContractError(
                "registry_entry_invalid",
                "framework_builtin extractor trust is reserved for framework "
                "implementations",
            )
        if trust_level is IngestorTrustLevel.WORKSPACE_ALLOWLISTED:
            configuration_fingerprint = getattr(
                extractor,
                "configuration_fingerprint",
                None,
            )
            try:
                validated_fingerprint = validate_fingerprint(
                    configuration_fingerprint,
                    field_name="configuration_fingerprint",
                )
            except IngestionContractError as exc:
                raise IngestionContractError(
                    "registry_entry_invalid",
                    "workspace allowlisted extractor requires a stable "
                    "configuration_fingerprint",
                ) from exc
            if (
                validated_fingerprint
                not in self._allowlisted_extractor_fingerprints
            ):
                raise IngestionContractError(
                    "extractor_not_allowlisted",
                    "workspace extractor configuration fingerprint is not "
                    "present in the registry allowlist",
                )
        with self._lock:
            if name in self._extractors and not replace:
                raise IngestionContractError(
                    "duplicate_identity",
                    f"extractor already registered: {name}",
                )
            self._extractors[name] = extractor

    def register_ingestor(
        self,
        ingestor: DatasetIngestor,
        *,
        replace: bool = False,
    ) -> None:
        name = _validate_name(getattr(ingestor, "name", None), kind="ingestor")
        version = getattr(ingestor, "version", None)
        if not isinstance(version, str) or not version:
            raise IngestionContractError(
                "registry_entry_invalid",
                "ingestor version must be non-empty",
            )
        if not callable(getattr(ingestor, "prepare", None)):
            raise IngestionContractError(
                "registry_entry_invalid",
                "ingestor does not implement the DatasetIngestor protocol",
            )
        if not isinstance(
            getattr(ingestor, "trust_level", None),
            IngestorTrustLevel,
        ):
            raise IngestionContractError(
                "registry_entry_invalid",
                "ingestor trust_level must use IngestorTrustLevel",
            )
        if (
            ingestor.trust_level is IngestorTrustLevel.FRAMEWORK_BUILTIN
            and not isinstance(ingestor, AgenticDatasetIngestor)
        ):
            raise IngestionContractError(
                "registry_entry_invalid",
                "framework_builtin ingestor trust is reserved for framework "
                "implementations",
            )
        if (
            ingestor.trust_level
            is IngestorTrustLevel.WORKSPACE_ALLOWLISTED
        ):
            configuration_fingerprint = getattr(
                ingestor,
                "configuration_fingerprint",
                None,
            )
            try:
                validated_fingerprint = validate_fingerprint(
                    configuration_fingerprint,
                    field_name="configuration_fingerprint",
                )
            except IngestionContractError as exc:
                raise IngestionContractError(
                    "registry_entry_invalid",
                    "workspace allowlisted ingestor requires a stable "
                    "configuration_fingerprint",
                ) from exc
            if (
                validated_fingerprint
                not in self._allowlisted_ingestor_fingerprints
            ):
                raise IngestionContractError(
                    "ingestor_not_allowlisted",
                    "workspace ingestor configuration fingerprint is not "
                    "present in the registry allowlist",
                )
        with self._lock:
            if name in self._ingestors and not replace:
                raise IngestionContractError(
                    "duplicate_identity",
                    f"ingestor already registered: {name}",
                )
            self._ingestors[name] = ingestor

    def get_extractor(self, name: str) -> DatasetExtractor:
        safe_name = _validate_name(name, kind="extractor")
        with self._lock:
            try:
                return self._extractors[safe_name]
            except KeyError as exc:
                raise IngestionContractError(
                    "extractor_not_registered",
                    f"extractor is not registered: {safe_name}",
                ) from exc

    def get_ingestor(self, name: str = "auto") -> DatasetIngestor:
        safe_name = _validate_name(name, kind="ingestor")
        with self._lock:
            try:
                return self._ingestors[safe_name]
            except KeyError as exc:
                raise IngestionContractError(
                    "ingestor_not_registered",
                    f"ingestor is not registered: {safe_name}",
                ) from exc

    def extractors(self) -> tuple[DatasetExtractor, ...]:
        with self._lock:
            return tuple(
                self._extractors[name] for name in sorted(self._extractors)
            )

    def ingestor_names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._ingestors))

    def extractor_names(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._extractors))

    def validate_snapshot_identity(
        self,
        snapshot: FrozenIngestionSnapshot,
        *,
        ingestor_name: str,
    ) -> DatasetIngestor:
        ingestor = self.get_ingestor(ingestor_name)
        expected = (
            ingestor.name,
            ingestor.version,
            ingestor.trust_level,
        )
        observed = (
            snapshot.ingestor_name,
            snapshot.ingestor_version,
            snapshot.ingestor_trust_level,
        )
        if observed != expected:
            raise IngestionContractError(
                "ingestor_identity_mismatch",
                "frozen snapshot ingestor identity does not match the "
                "registered strategy",
            )
        return ingestor

    def effective_snapshot_trust_level(
        self,
        snapshot: FrozenIngestionSnapshot,
        *,
        ingestor_name: str,
    ) -> IngestorTrustLevel:
        ingestor = self.validate_snapshot_identity(
            snapshot,
            ingestor_name=ingestor_name,
        )
        trust_levels = [ingestor.trust_level]
        expected_extractor_fingerprints: set[str] = set()
        for asset in snapshot.inventory.assets:
            if asset.extractor_name is None:
                continue
            extractor = self.get_extractor(asset.extractor_name)
            if extractor.version != asset.extractor_version:
                raise IngestionContractError(
                    "extractor_identity_mismatch",
                    "frozen source asset extractor version does not match "
                    "the registry",
                )
            expected_extractor_fingerprints.add(
                extractor_fingerprint(extractor)
            )
            trust_levels.append(
                getattr(
                    extractor,
                    "trust_level",
                    IngestorTrustLevel.EXTERNAL_UNTRUSTED,
                )
            )
        if expected_extractor_fingerprints != set(
            snapshot.extractor_fingerprints
        ):
            raise IngestionContractError(
                "extractor_identity_mismatch",
                "frozen extractor fingerprints do not match the registry",
            )
        if IngestorTrustLevel.EXTERNAL_UNTRUSTED in trust_levels:
            return IngestorTrustLevel.EXTERNAL_UNTRUSTED
        if IngestorTrustLevel.WORKSPACE_ALLOWLISTED in trust_levels:
            return IngestorTrustLevel.WORKSPACE_ALLOWLISTED
        return IngestorTrustLevel.FRAMEWORK_BUILTIN


def _validate_name(value: object, *, kind: str) -> str:
    if not isinstance(value, str) or not _REGISTERED_NAME.fullmatch(value):
        raise IngestionContractError(
            "unsafe_identity",
            f"{kind} name must be a stable registered name",
        )
    if ":" in value or "." in value:
        raise IngestionContractError(
            "dynamic_import_not_allowed",
            f"{kind} import strings are not accepted",
        )
    return value


_DEFAULT_EXTRACTORS = builtin_extractors()
DEFAULT_INGESTION_REGISTRY = IngestionRegistry(
    extractors=_DEFAULT_EXTRACTORS,
    ingestors=(AgenticDatasetIngestor(extractors=_DEFAULT_EXTRACTORS),),
)
