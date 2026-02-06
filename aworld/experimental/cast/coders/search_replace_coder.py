"""
Search Replace Coder - Precise search and replace operations

This coder handles search-and-replace operations with exact matching,
extracted from ACast.search_replace_operation method.
"""

import json
import math
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, Any, Union, List, Optional, Tuple

from aworld.logs.util import logger
from .base_coder import BaseCoder, CoderResult, CoderValidationError, CoderOperationError


class SearchReplaceCoder(BaseCoder):
    """
    Coder for search-and-replace operations with fuzzy matching support

    This implementation is based on aider's search-replace algorithm with
    support for exact matching, whitespace-flexible matching, and similarity-based
    fuzzy matching. Extracted from ACast.search_replace_operation.
    """

    def __init__(self,
                 source_dir: Path,
                 validation_enabled: bool = True,
                 dry_run: bool = False,
                 backup_enabled: bool = True,
                 fuzzy_match_enabled: bool = False,
                 similarity_threshold: float = 0.8):
        """
        Initialize search-replace coder

        Args:
            source_dir: Directory containing source code to modify
            validation_enabled: Whether to perform input validation
            dry_run: If True, perform validation but don't make actual changes
            backup_enabled: Whether to create backups before modifications
            fuzzy_match_enabled: Whether to enable fuzzy matching (default: False for safety)
            similarity_threshold: Threshold for fuzzy matching (0.0-1.0)
        """
        super().__init__(source_dir, validation_enabled, dry_run, backup_enabled)
        self.fuzzy_match_enabled = fuzzy_match_enabled
        self.similarity_threshold = max(0.0, min(1.0, similarity_threshold))

        if fuzzy_match_enabled:
            logger.warning(
                "Fuzzy matching enabled - this may lead to unintended replacements. "
                "Exact matching is recommended for safety."
            )

    def get_supported_operation_types(self) -> List[str]:
        """Get supported operation types"""
        return ["search_replace"]

    def validate_operation_data(self, operation_data: Union[str, Dict[str, Any]]) -> bool:
        """
        Validate search-replace operation data

        Args:
            operation_data: JSON string or dict containing operation data

        Returns:
            True if validation passes

        Raises:
            CoderValidationError: If validation fails
        """
        try:
            # Parse if string
            if isinstance(operation_data, str):
                data = json.loads(operation_data)
            else:
                data = operation_data

            # Check required structure
            if "operation" not in data:
                raise CoderValidationError("Missing 'operation' field")

            operation = data["operation"]

            # Check required fields
            required_fields = ["type", "file_path", "search", "replace"]
            for field in required_fields:
                if field not in operation:
                    raise CoderValidationError(f"Missing required field: {field}")

            # Check operation type
            if operation["type"] != "search_replace":
                raise CoderValidationError(
                    f"Unsupported operation type: {operation['type']}, expected 'search_replace'"
                )

            # Validate file path
            file_path = self.source_dir / operation["file_path"]
            if not file_path.exists():
                raise CoderValidationError(f"Target file does not exist: {file_path}")

            # Validate search text is not empty
            if not operation["search"].strip():
                raise CoderValidationError("Search text cannot be empty")

            return True

        except json.JSONDecodeError as e:
            raise CoderValidationError(f"Invalid JSON format: {e}")
        except Exception as e:
            raise CoderValidationError(f"Validation failed: {e}")

    def execute(self, operation_data: Union[str, Dict[str, Any]]) -> CoderResult:
        """
        Execute search-replace operation

        Args:
            operation_data: JSON string or dict containing:
                {
                    "operation": {
                        "type": "search_replace",
                        "file_path": "relative/path/to/file.py",
                        "search": "code to search for",
                        "replace": "replacement code",
                        "exact_match_only": true  # optional, default true
                    }
                }

        Returns:
            CoderResult containing operation results

        Raises:
            CoderValidationError: If input validation fails
            CoderOperationError: If operation execution fails
        """
        # Validate input
        if self.validation_enabled:
            self.validate_operation_data(operation_data)

        try:
            # Parse operation data
            if isinstance(operation_data, str):
                data = json.loads(operation_data)
            else:
                data = operation_data

            operation = data["operation"]
            file_path = self.source_dir / operation["file_path"]
            search_text = operation["search"]
            replace_text = operation["replace"]

            # Check if fuzzy matching is explicitly disabled in operation
            exact_match_only = operation.get("exact_match_only", True)
            use_fuzzy = self.fuzzy_match_enabled and not exact_match_only

            logger.info(f"Executing search-replace on: {file_path}")
            logger.debug(f"Search text length: {len(search_text)} chars")
            logger.debug(f"Replace text length: {len(replace_text)} chars")
            logger.debug(f"Fuzzy matching: {'enabled' if use_fuzzy else 'disabled'}")

            # Read original file content
            try:
                original_content = file_path.read_text(encoding='utf-8')
            except Exception as e:
                raise CoderOperationError(f"Failed to read file {file_path}: {e}")

            # Create backup if enabled
            backup_path = None
            if not self.dry_run:
                backup_path = self.create_backup(file_path)

            # Perform search-replace
            try:
                new_content = self._fuzzy_search_replace(
                    original_content,
                    search_text,
                    replace_text,
                    use_fuzzy,
                    self.similarity_threshold
                )

                if new_content is None:
                    return self._create_error_result(
                        "No matching content found for replacement",
                        original_content=original_content,
                        search_text=search_text,
                        fuzzy_enabled=use_fuzzy
                    )

                # Write new content if not dry run
                if not self.dry_run:
                    try:
                        file_path.write_text(new_content, encoding='utf-8')
                        logger.info(f"✅ Search-replace completed successfully: {file_path}")
                    except Exception as e:
                        # Restore from backup if write fails
                        if backup_path:
                            self.restore_from_backup(file_path)
                        raise CoderOperationError(f"Failed to write modified content: {e}")
                else:
                    logger.info(f"🔍 Dry run: Search-replace would modify {file_path}")

                return self._create_success_result(
                    modified=True,
                    message=f"Search-replace {'simulated' if self.dry_run else 'completed'} successfully",
                    original_content=original_content,
                    new_content=new_content,
                    file_path=str(file_path),
                    search_text=search_text,
                    replace_text=replace_text,
                    fuzzy_enabled=use_fuzzy,
                    backup_path=str(backup_path) if backup_path else None
                )

            except Exception as e:
                # Restore from backup if operation fails
                if backup_path and not self.dry_run:
                    self.restore_from_backup(file_path)
                raise CoderOperationError(f"Search-replace operation failed: {e}")

        except json.JSONDecodeError as e:
            raise CoderValidationError(f"Invalid JSON format: {e}")
        except Exception as e:
            if isinstance(e, (CoderValidationError, CoderOperationError)):
                raise
            raise CoderOperationError(f"Unexpected error during search-replace: {e}")

    def _fuzzy_search_replace(self,
                             content: str,
                             search_text: str,
                             replace_text: str,
                             fuzzy_match: bool = False,
                             similarity_threshold: float = 1.0) -> Optional[str]:
        """
        Precise search-replace algorithm - supports exact matching primarily

        Strategy priorities:
        1. Exact matching (recommended)
        2. If fuzzy_match enabled, flexible whitespace matching (not recommended)

        Args:
            content: File content
            search_text: Text to search for
            replace_text: Replacement text
            fuzzy_match: Whether to enable flexible whitespace matching (default False)
            similarity_threshold: Ignored (kept for interface compatibility)

        Returns:
            Modified content if match found, None if no match
        """
        if not search_text.strip():
            return None

        # Prepare content and search text
        content, content_lines = self._prep_text(content)
        search_text, search_lines = self._prep_text(search_text)
        replace_text, replace_lines = self._prep_text(replace_text)

        # Strategy 1: Exact matching (primary strategy)
        result = self._perfect_replace(content_lines, search_lines, replace_lines)
        if result:
            logger.info("✅ Using exact matching strategy")
            return result

        result = self._inner_perfect_replace(content_lines, search_lines, replace_lines)
        if result:
            logger.info("✅ Using inner exact matching strategy")
            return result

        if fuzzy_match:
            # Strategy 2: Whitespace flexible matching (only when explicitly enabled)
            result = self._whitespace_flexible_replace(content_lines, search_lines, replace_lines)
            if result:
                logger.warning("⚠️ Using whitespace flexible matching strategy (not recommended)")
                return result

            # Fuzzy matching
            result = self._similarity_replace(
                content_lines=content_lines,
                search_text="".join(search_lines),
                search_lines=search_lines,
                replace_lines=replace_lines,
                threshold=similarity_threshold
            )
            if result:
                logger.warning("⚠️ Using fuzzy matching strategy (not recommended)")
                return result

        return None

    def _prep_text(self, text: str) -> Tuple[str, List[str]]:
        """Prepare text ensuring it ends with newline and split into lines"""
        if text and not text.endswith("\n"):
            text += "\n"
        lines = text.splitlines(keepends=True)
        return text, lines

    def _perfect_replace(self, content_lines: List[str], search_lines: List[str], replace_lines: List[str]) -> Optional[str]:
        """Exact matching replacement - based on aider's perfect_replace algorithm"""
        search_tuple = tuple(search_lines)
        search_len = len(search_lines)

        for i in range(len(content_lines) - search_len + 1):
            content_tuple = tuple(content_lines[i:i + search_len])
            if search_tuple == content_tuple:
                # Found exact match, perform replacement
                result_lines = content_lines[:i] + replace_lines + content_lines[i + search_len:]
                return "".join(result_lines)

        return None

    def _inner_perfect_replace(self, content_lines: List[str], search_lines: List[str], replace_lines: List[str]) -> Optional[str]:
        """Inner exact matching replacement"""
        content = "".join(content_lines).strip()
        search = "".join(search_lines).strip()
        if search in content:
            return content.replace(search, "".join(replace_lines))

        return None

    def _whitespace_flexible_replace(self, content_lines: List[str], search_lines: List[str], replace_lines: List[str]) -> Optional[str]:
        """Whitespace flexible matching - based on aider's whitespace matching algorithm"""
        # Calculate minimum common indentation
        leading_spaces = []
        for line in search_lines + replace_lines:
            if line.strip():  # Only consider non-empty lines
                leading_spaces.append(len(line) - len(line.lstrip()))

        if not leading_spaces:
            return None

        # Remove common indentation
        min_indent = min(leading_spaces) if leading_spaces else 0
        if min_indent > 0:
            normalized_search = [line[min_indent:] if line.strip() else line for line in search_lines]
            normalized_replace = [line[min_indent:] if line.strip() else line for line in replace_lines]
        else:
            normalized_search = search_lines
            normalized_replace = replace_lines

        # Find match (ignoring indentation)
        for i in range(len(content_lines) - len(normalized_search) + 1):
            match_indent = self._check_indent_match(
                content_lines[i:i + len(normalized_search)],
                normalized_search
            )

            if match_indent is not None:
                # Apply same indentation to replacement text
                adjusted_replace = [
                    match_indent + line if line.strip() else line
                    for line in normalized_replace
                ]
                result_lines = content_lines[:i] + adjusted_replace + content_lines[i + len(normalized_search):]
                return "".join(result_lines)

        return None

    def _check_indent_match(self, content_section: List[str], search_section: List[str]) -> Optional[str]:
        """Check if content section matches search section (ignoring indentation)"""
        if len(content_section) != len(search_section):
            return None

        # Check if content matches after removing indentation
        for content_line, search_line in zip(content_section, search_section):
            if content_line.lstrip() != search_line.lstrip():
                return None

        # Calculate unified indentation prefix
        indents = set()
        for content_line, search_line in zip(content_section, search_section):
            if content_line.strip():  # Only consider non-empty lines
                content_indent = content_line[:len(content_line) - len(content_line.lstrip())]
                search_indent = search_line[:len(search_line) - len(search_line.lstrip())]
                indent_diff = content_indent[len(search_indent):] if len(content_indent) >= len(search_indent) else ""
                indents.add(indent_diff)

        if len(indents) == 1:
            return indents.pop()
        return None

    def _similarity_replace(self,
                           content_lines: List[str],
                           search_text: str,
                           search_lines: List[str],
                           replace_lines: List[str],
                           threshold: float) -> Optional[str]:
        """Similarity-based fuzzy matching - based on aider's similarity matching algorithm"""
        max_similarity = 0.0
        best_match_start = -1
        best_match_end = -1

        # Search range: allow 10% length variation
        search_len = len(search_lines)
        min_len = math.floor(search_len * 0.9)
        max_len = math.ceil(search_len * 1.1)

        for length in range(min_len, max_len + 1):
            for i in range(len(content_lines) - length + 1):
                chunk_lines = content_lines[i:i + length]
                chunk_text = "".join(chunk_lines)

                # Calculate similarity
                similarity = SequenceMatcher(None, chunk_text, search_text).ratio()

                if similarity > max_similarity and similarity >= threshold:
                    max_similarity = similarity
                    best_match_start = i
                    best_match_end = i + length

        if best_match_start >= 0:
            logger.info(f"🎯 Found fuzzy match (similarity: {max_similarity:.3f})")
            result_lines = (content_lines[:best_match_start] +
                           replace_lines +
                           content_lines[best_match_end:])
            return "".join(result_lines)

        return None