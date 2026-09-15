"""Resource limits shared by the production filesystem tools.

The MCP client bounds what is eventually shown to a model, but that happens after
the server has read and serialized a result.  These limits therefore apply at the
producer so a large file, directory, or search result cannot exhaust the tool
server first.  They are intentionally configurable for general CLI users while
remaining finite in every configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
import os


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


@dataclass(frozen=True)
class FilesystemLimits:
    """Hard producer-side budgets for one filesystem tool invocation."""

    max_read_bytes: int
    max_binary_bytes: int
    max_line_bytes: int
    max_scan_bytes: int
    max_result_lines: int
    max_list_entries: int
    max_search_matches: int
    max_search_per_file: int
    max_search_files: int
    max_search_output_bytes: int
    max_search_depth: int
    search_timeout_seconds: float
    max_edit_bytes: int
    max_parse_bytes: int
    max_parse_output_bytes: int
    max_parse_memory_bytes: int
    parse_timeout_seconds: float
    max_archive_members: int
    max_archive_member_bytes: int
    max_archive_uncompressed_bytes: int
    max_archive_compression_ratio: int
    max_workbook_sheets: int
    max_workbook_rows: int
    max_workbook_columns: int
    max_workbook_cells: int
    max_workbook_merged_cells: int

    @classmethod
    def from_env(cls) -> "FilesystemLimits":
        """Load limits for the current call, allowing tests and CLIs to tune them."""

        mib = 1024 * 1024
        return cls(
            max_read_bytes=_env_int(
                "AWORLD_FILESYSTEM_MAX_READ_BYTES", mib, minimum=4096, maximum=64 * mib
            ),
            max_binary_bytes=_env_int(
                "AWORLD_FILESYSTEM_MAX_BINARY_BYTES", 4 * mib, minimum=4096, maximum=64 * mib
            ),
            max_line_bytes=_env_int(
                "AWORLD_FILESYSTEM_MAX_LINE_BYTES", 256 * 1024, minimum=1024, maximum=8 * mib
            ),
            max_scan_bytes=_env_int(
                "AWORLD_FILESYSTEM_MAX_SCAN_BYTES", 64 * mib, minimum=64 * 1024, maximum=1024 * mib
            ),
            max_result_lines=_env_int(
                "AWORLD_FILESYSTEM_MAX_RESULT_LINES", 20_000, minimum=1, maximum=1_000_000
            ),
            max_list_entries=_env_int(
                "AWORLD_FILESYSTEM_MAX_LIST_ENTRIES", 2_000, minimum=1, maximum=100_000
            ),
            max_search_matches=_env_int(
                "AWORLD_FILESYSTEM_MAX_SEARCH_MATCHES", 1_000, minimum=1, maximum=100_000
            ),
            max_search_per_file=_env_int(
                "AWORLD_FILESYSTEM_MAX_SEARCH_PER_FILE", 200, minimum=1, maximum=100_000
            ),
            max_search_files=_env_int(
                "AWORLD_FILESYSTEM_MAX_SEARCH_FILES", 10_000, minimum=1, maximum=1_000_000
            ),
            max_search_output_bytes=_env_int(
                "AWORLD_FILESYSTEM_MAX_SEARCH_OUTPUT_BYTES", mib, minimum=4096, maximum=64 * mib
            ),
            max_search_depth=_env_int(
                "AWORLD_FILESYSTEM_MAX_SEARCH_DEPTH", 128, minimum=1, maximum=1024
            ),
            search_timeout_seconds=_env_float(
                "AWORLD_FILESYSTEM_SEARCH_TIMEOUT_SECONDS", 10.0, minimum=0.1, maximum=300.0
            ),
            max_edit_bytes=_env_int(
                "AWORLD_FILESYSTEM_MAX_EDIT_BYTES", 16 * mib, minimum=4096, maximum=256 * mib
            ),
            max_parse_bytes=_env_int(
                "AWORLD_FILESYSTEM_MAX_PARSE_BYTES", 64 * mib, minimum=4096, maximum=1024 * mib
            ),
            max_parse_output_bytes=_env_int(
                "AWORLD_FILESYSTEM_MAX_PARSE_OUTPUT_BYTES", 16 * mib, minimum=4096, maximum=256 * mib
            ),
            max_parse_memory_bytes=_env_int(
                "AWORLD_FILESYSTEM_MAX_PARSE_MEMORY_BYTES", 1024 * mib, minimum=128 * mib, maximum=4 * 1024 * mib
            ),
            parse_timeout_seconds=_env_float(
                "AWORLD_FILESYSTEM_PARSE_TIMEOUT_SECONDS", 60.0, minimum=1.0, maximum=300.0
            ),
            max_archive_members=_env_int(
                "AWORLD_FILESYSTEM_MAX_ARCHIVE_MEMBERS", 4096, minimum=1, maximum=100_000
            ),
            max_archive_member_bytes=_env_int(
                "AWORLD_FILESYSTEM_MAX_ARCHIVE_MEMBER_BYTES", 32 * mib, minimum=4096, maximum=512 * mib
            ),
            max_archive_uncompressed_bytes=_env_int(
                "AWORLD_FILESYSTEM_MAX_ARCHIVE_UNCOMPRESSED_BYTES", 256 * mib, minimum=4096, maximum=2 * 1024 * mib
            ),
            max_archive_compression_ratio=_env_int(
                "AWORLD_FILESYSTEM_MAX_ARCHIVE_COMPRESSION_RATIO", 200, minimum=1, maximum=10_000
            ),
            max_workbook_sheets=_env_int(
                "AWORLD_FILESYSTEM_MAX_WORKBOOK_SHEETS", 256, minimum=1, maximum=10_000
            ),
            max_workbook_rows=_env_int(
                "AWORLD_FILESYSTEM_MAX_WORKBOOK_ROWS", 100_000, minimum=1, maximum=1_048_576
            ),
            max_workbook_columns=_env_int(
                "AWORLD_FILESYSTEM_MAX_WORKBOOK_COLUMNS", 1_000, minimum=1, maximum=16_384
            ),
            max_workbook_cells=_env_int(
                "AWORLD_FILESYSTEM_MAX_WORKBOOK_CELLS", 2_000_000, minimum=1, maximum=20_000_000
            ),
            max_workbook_merged_cells=_env_int(
                "AWORLD_FILESYSTEM_MAX_WORKBOOK_MERGED_CELLS", 2_000_000, minimum=1, maximum=20_000_000
            ),
        )


def truncation_notice(*, reason: str, limit: int | float) -> str:
    """Stable, model-readable marker used by legacy plain-text tool results."""

    return f"[TRUNCATED] reason={reason}; limit={limit}"
