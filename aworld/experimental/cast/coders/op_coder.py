"""
Op (Operations) Coder - JSON operations to patch deployment

This coder handles JSON-based operation instructions and converts them to patches,
extracted from ACast.deploy_operations method.
"""

import difflib
import json
from pathlib import Path
from typing import Dict, Any, Union, List

from ..utils import logger
from .base_coder import BaseCoder, CoderResult, CoderValidationError, CoderOperationError
from .dmp_coder import DmpCoder


class OpCoder(BaseCoder):
    """
    Coder for JSON-based operation deployment

    This implementation converts JSON operation instructions to unified diff patches
    and applies them using DmpCoder. Extracted from ACast.deploy_operations method.

    Supports operation types:
    - insert: Insert code after specified line
    - replace: Replace specified line range with new content
    - delete: Delete specified line range
    """

    def __init__(self,
                 source_dir: Path,
                 validation_enabled: bool = True,
                 dry_run: bool = False,
                 backup_enabled: bool = True,
                 strict_validation: bool = True,
                 max_context_mismatches: int = 0):
        """
        Initialize operations coder

        Args:
            source_dir: Directory containing source code to modify
            validation_enabled: Whether to perform input validation
            dry_run: If True, perform validation but don't make actual changes
            backup_enabled: Whether to create backups before modifications
            strict_validation: Whether to enable strict validation mode
            max_context_mismatches: Maximum allowed context mismatches
        """
        super().__init__(source_dir, validation_enabled, dry_run, backup_enabled)
        self.strict_validation = strict_validation
        self.max_context_mismatches = max_context_mismatches

        # Create DMP coder instance for patch application
        self.dmp_coder = DmpCoder(
            source_dir=source_dir,
            validation_enabled=validation_enabled,
            dry_run=dry_run,
            backup_enabled=backup_enabled,
            strict_validation=strict_validation,
            max_context_mismatches=max_context_mismatches
        )

    def get_supported_operation_types(self) -> List[str]:
        """Get supported operation types"""
        return ["deploy_operations", "json_operations"]

    def validate_operation_data(self, operation_data: Union[str, Dict[str, Any]]) -> bool:
        """
        Validate JSON operations data

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
            if "operations" not in data:
                raise CoderValidationError("Missing 'operations' field")

            operations = data["operations"]

            if not isinstance(operations, list):
                raise CoderValidationError("'operations' must be a list")

            if not operations:
                raise CoderValidationError("Operations list cannot be empty")

            # Validate each operation
            for i, op in enumerate(operations):
                self._validate_single_operation(op, i)

            return True

        except json.JSONDecodeError as e:
            raise CoderValidationError(f"Invalid JSON format: {e}")
        except Exception as e:
            raise CoderValidationError(f"Validation failed: {e}")

    def _validate_single_operation(self, operation: Dict[str, Any], index: int):
        """
        Validate a single operation

        Args:
            operation: Single operation dictionary
            index: Operation index for error reporting

        Raises:
            CoderValidationError: If validation fails
        """
        # Check required fields
        if "type" not in operation:
            raise CoderValidationError(f"Operation {index}: Missing 'type' field")

        if "file_path" not in operation:
            raise CoderValidationError(f"Operation {index}: Missing 'file_path' field")

        op_type = operation["type"]

        # Validate operation type
        if op_type not in ["insert", "replace", "delete"]:
            raise CoderValidationError(
                f"Operation {index}: Unsupported operation type '{op_type}', "
                f"expected one of: insert, replace, delete"
            )

        # Validate file path
        file_path = self.source_dir / operation["file_path"]
        if not file_path.exists():
            raise CoderValidationError(f"Operation {index}: Target file does not exist: {file_path}")

        # Type-specific validation
        if op_type == "insert":
            if "after_line" not in operation:
                raise CoderValidationError(f"Operation {index}: 'insert' requires 'after_line' field")

            if "content" not in operation:
                raise CoderValidationError(f"Operation {index}: 'insert' requires 'content' field")

            if not isinstance(operation["content"], list):
                raise CoderValidationError(f"Operation {index}: 'content' must be a list of strings")

        elif op_type == "replace":
            if "start_line" not in operation:
                raise CoderValidationError(f"Operation {index}: 'replace' requires 'start_line' field")

            if "end_line" not in operation:
                raise CoderValidationError(f"Operation {index}: 'replace' requires 'end_line' field")

            if "content" not in operation:
                raise CoderValidationError(f"Operation {index}: 'replace' requires 'content' field")

            if not isinstance(operation["content"], list):
                raise CoderValidationError(f"Operation {index}: 'content' must be a list of strings")

        elif op_type == "delete":
            if "start_line" not in operation:
                raise CoderValidationError(f"Operation {index}: 'delete' requires 'start_line' field")

            if "end_line" not in operation:
                raise CoderValidationError(f"Operation {index}: 'delete' requires 'end_line' field")

    def execute(self, operation_data: Union[str, Dict[str, Any]]) -> CoderResult:
        """
        Execute JSON operations deployment

        Args:
            operation_data: JSON string or dict containing:
                {
                    "operations": [
                        {
                            "type": "insert",
                            "file_path": "example.py",
                            "after_line": 10,
                            "content": ["new line 1", "new line 2"]
                        },
                        {
                            "type": "replace",
                            "file_path": "example.py",
                            "start_line": 15,
                            "end_line": 20,
                            "content": ["replacement content"]
                        },
                        {
                            "type": "delete",
                            "file_path": "example.py",
                            "start_line": 25,
                            "end_line": 30
                        }
                    ],
                    "version": "v1",  # optional, default "v0"
                    "strict_validation": true,  # optional, override default
                    "max_context_mismatches": 0  # optional, override default
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
                operations_json = operation_data
            else:
                data = operation_data
                operations_json = json.dumps(data, ensure_ascii=False, indent=2)

            version = data.get("version", "v0")

            # Override validation settings if specified
            strict_validation = data.get("strict_validation", self.strict_validation)
            max_context_mismatches = data.get("max_context_mismatches", self.max_context_mismatches)

            logger.info(f"🚀 Starting JSON operations deployment")
            logger.debug(f"Operations count: {len(data['operations'])}")
            logger.debug(f"Version: {version}")

            # Convert JSON operations to patch format
            patch_content = self.json_operations_to_patch(operations_json, self.source_dir)

            if not patch_content.strip():
                logger.info("📋 No code changes detected, skipping deployment")
                return self._create_success_result(
                    modified=False,
                    message="No changes to apply",
                    operations_json=operations_json,
                    patch_content="",
                    version=version
                )

            logger.info(f"📝 Generated patch content, length: {len(patch_content)} characters")

            # Use DMP coder to apply the patch
            patch_operation = {
                "operation": {
                    "type": "apply_patch",
                    "patch_content": patch_content,
                    "version": version,
                    "strict_validation": strict_validation,
                    "max_context_mismatches": max_context_mismatches
                }
            }

            dmp_result = self.dmp_coder.execute(patch_operation)

            if dmp_result.success:
                return self._create_success_result(
                    modified=dmp_result.modified,
                    message=f"Operations deployment {'simulated' if self.dry_run else 'completed'} successfully",
                    original_content=dmp_result.original_content,
                    new_content=dmp_result.new_content,
                    operations_json=operations_json,
                    patch_content=patch_content,
                    version=version,
                    modified_files=dmp_result.metadata.get("modified_files", []),
                    dmp_result=dmp_result.metadata
                )
            else:
                return self._create_error_result(
                    f"Patch application failed: {dmp_result.error}",
                    operations_json=operations_json,
                    patch_content=patch_content,
                    dmp_result=dmp_result.metadata
                )

        except json.JSONDecodeError as e:
            raise CoderValidationError(f"Invalid JSON format: {e}")
        except Exception as e:
            if isinstance(e, (CoderValidationError, CoderOperationError)):
                raise
            raise CoderOperationError(f"Unexpected error during operations deployment: {e}")

    def json_operations_to_patch(self, operations_json: str, source_dir: Path) -> str:
        """
        Convert JSON format operation instructions to unified diff format patch content

        Args:
            operations_json: JSON format operation instruction string
            source_dir: Source code directory path

        Returns:
            Unified diff format patch content

        Example:
            Operations JSON format:
            {
                "operations": [
                    {
                        "type": "insert",
                        "file_path": "example.py",
                        "after_line": 10,
                        "content": ["new line 1", "new line 2"]
                    },
                    {
                        "type": "replace",
                        "file_path": "example.py",
                        "start_line": 15,
                        "end_line": 20,
                        "content": ["replacement content"]
                    },
                    {
                        "type": "delete",
                        "file_path": "example.py",
                        "start_line": 25,
                        "end_line": 30
                    }
                ]
            }
        """
        try:
            operations_data = json.loads(operations_json)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON format: {e}")

        if "operations" not in operations_data:
            raise ValueError("JSON missing 'operations' field")

        operations = operations_data["operations"]

        # Group operations by file path, ensure each file's operations are sorted by line number
        file_operations = {}
        for op in operations:
            if "file_path" not in op or "type" not in op:
                raise ValueError("Operation missing required 'file_path' or 'type' field")

            file_path = op["file_path"]
            if file_path not in file_operations:
                file_operations[file_path] = []
            file_operations[file_path].append(op)

        # Sort each file's operations by line number (reverse order to avoid line offset)
        for file_path in file_operations:
            file_operations[file_path].sort(key=self._get_operation_sort_key, reverse=True)

        all_diffs = []

        # Process each file
        for file_path, ops in file_operations.items():
            full_file_path = source_dir / file_path

            if not full_file_path.exists():
                logger.warning(f"File does not exist, skipping: {full_file_path}")
                continue

            try:
                # Read original file content
                with open(full_file_path, 'r', encoding='utf-8') as f:
                    original_lines = f.readlines()

                # Apply all operations to content copy
                modified_lines = original_lines.copy()

                for op in ops:
                    modified_lines = self._apply_single_operation(modified_lines, op)

                # Generate unified diff
                original_content = ''.join(original_lines)
                modified_content = ''.join(modified_lines)

                diff_lines = list(difflib.unified_diff(
                    original_content.splitlines(keepends=True),
                    modified_content.splitlines(keepends=True),
                    fromfile=f"a/{file_path}",
                    tofile=f"b/{file_path}",
                    lineterm=''
                ))

                if diff_lines:  # Only add non-empty diffs
                    # Ensure proper formatting with newlines
                    diff_content = ''.join(line if line.endswith('\n') else line + '\n' for line in diff_lines)
                    all_diffs.append(diff_content.rstrip())  # Remove trailing newline to avoid double newlines

            except Exception as e:
                logger.error(f"Error processing file {file_path}: {e}")
                raise

        if not all_diffs:
            return ""  # No changes

        return '\n'.join(all_diffs)

    def _get_operation_sort_key(self, op: dict) -> int:
        """Get operation sort key for sorting by line number"""
        if op["type"] == "insert":
            return op.get("after_line", 0)
        elif op["type"] in ["replace", "delete"]:
            return op.get("start_line", 0)
        else:
            return 0

    def _apply_single_operation(self, lines: List[str], op: dict) -> List[str]:
        """
        Apply single operation to line list

        Args:
            lines: File line list (each line includes newline)
            op: Single operation dictionary

        Returns:
            Line list after applying operation
        """
        op_type = op["type"]

        if op_type == "insert":
            after_line = op.get("after_line", 0)
            content = op.get("content", [])

            if after_line < 0 or after_line > len(lines):
                raise ValueError(f"Invalid insert position: after_line={after_line}, file has {len(lines)} lines")

            # Ensure content lines end with newline
            insert_lines = [line if line.endswith('\n') else line + '\n' for line in content]

            # Insert after specified line
            return lines[:after_line] + insert_lines + lines[after_line:]

        elif op_type == "replace":
            start_line = op.get("start_line", 1)
            end_line = op.get("end_line", start_line)
            content = op.get("content", [])

            if start_line < 1 or end_line < start_line or start_line > len(lines):
                raise ValueError(f"Invalid replace range: start_line={start_line}, end_line={end_line}, file has {len(lines)} lines")

            # Convert to 0-based index
            start_idx = start_line - 1
            end_idx = min(end_line, len(lines))

            # Ensure content lines end with newline
            replace_lines = [line if line.endswith('\n') else line + '\n' for line in content]

            # Replace specified range
            return lines[:start_idx] + replace_lines + lines[end_idx:]

        elif op_type == "delete":
            start_line = op.get("start_line", 1)
            end_line = op.get("end_line", start_line)

            if start_line < 1 or end_line < start_line or start_line > len(lines):
                raise ValueError(f"Invalid delete range: start_line={start_line}, end_line={end_line}, file has {len(lines)} lines")

            # Convert to 0-based index
            start_idx = start_line - 1
            end_idx = min(end_line, len(lines))

            # Delete specified range
            return lines[:start_idx] + lines[end_idx:]

        else:
            raise ValueError(f"Unsupported operation type: {op_type}")

    def deploy_operations(self,
                         operations_json: str,
                         source_dir: Path = None,
                         version: str = "v0",
                         strict_validation: bool = None,
                         max_context_mismatches: int = None) -> CoderResult:
        """
        Convenience method that mimics ACast.deploy_operations behavior

        Args:
            operations_json: JSON format operation instructions
            source_dir: Source code directory (optional, uses instance default)
            version: Version number
            strict_validation: Override strict validation setting
            max_context_mismatches: Override max context mismatches setting

        Returns:
            CoderResult containing operation results
        """
        # Parse operations data and add configuration
        try:
            data = json.loads(operations_json)
        except json.JSONDecodeError as e:
            raise CoderValidationError(f"Invalid JSON format: {e}")

        # Add configuration to data
        data["version"] = version
        if strict_validation is not None:
            data["strict_validation"] = strict_validation
        if max_context_mismatches is not None:
            data["max_context_mismatches"] = max_context_mismatches

        # Temporarily override source_dir if different
        original_source_dir = self.source_dir
        if source_dir and source_dir != self.source_dir:
            self.source_dir = Path(source_dir)
            # Also update DMP coder's source_dir
            self.dmp_coder.source_dir = self.source_dir

        try:
            return self.execute(data)
        finally:
            # Restore original source_dir
            self.source_dir = original_source_dir
            self.dmp_coder.source_dir = original_source_dir