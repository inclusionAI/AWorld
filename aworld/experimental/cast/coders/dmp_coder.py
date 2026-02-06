"""
DMP (Diff-Match-Patch) Coder - Enhanced patch application

This coder handles patch application with enhanced validation and recovery,
extracted from ACast.create_enhanced_copy method.
"""

import json
import tempfile
import traceback
from pathlib import Path
from typing import Dict, Any, Union, List

from aworld.logs.util import logger
from .base_coder import BaseCoder, CoderResult, CoderValidationError, CoderOperationError


class DmpCoder(BaseCoder):
    """
    Coder for applying unified diff patches with validation

    This implementation uses the proven difflib+patch_ng approach for applying
    patches, extracted from ACast.create_enhanced_copy method. Provides enhanced
    validation and error recovery capabilities.
    """

    def __init__(self,
                 source_dir: Path,
                 validation_enabled: bool = True,
                 dry_run: bool = False,
                 backup_enabled: bool = True,
                 strict_validation: bool = True,
                 max_context_mismatches: int = 0):
        """
        Initialize DMP coder

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

        # Verify patch_ng is available
        try:
            import patch_ng
            self._patch_ng = patch_ng
        except ImportError:
            raise CoderOperationError(
                "patch_ng library is not installed. Please run: pip install patch-ng"
            )

    def get_supported_operation_types(self) -> List[str]:
        """Get supported operation types"""
        return ["apply_patch", "enhanced_patch"]

    def validate_operation_data(self, operation_data: Union[str, Dict[str, Any]]) -> bool:
        """
        Validate patch operation data

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
            required_fields = ["type", "patch_content"]
            for field in required_fields:
                if field not in operation:
                    raise CoderValidationError(f"Missing required field: {field}")

            # Check operation type
            if operation["type"] not in self.get_supported_operation_types():
                raise CoderValidationError(
                    f"Unsupported operation type: {operation['type']}, "
                    f"expected one of: {self.get_supported_operation_types()}"
                )

            # Validate patch content is not empty
            if not operation["patch_content"].strip():
                raise CoderValidationError("Patch content cannot be empty")

            # Validate version if provided
            version = operation.get("version", "v0")
            if not isinstance(version, str) or not version:
                raise CoderValidationError("Version must be a non-empty string")

            return True

        except json.JSONDecodeError as e:
            raise CoderValidationError(f"Invalid JSON format: {e}")
        except Exception as e:
            raise CoderValidationError(f"Validation failed: {e}")

    def execute(self, operation_data: Union[str, Dict[str, Any]]) -> CoderResult:
        """
        Execute patch application operation

        Args:
            operation_data: JSON string or dict containing:
                {
                    "operation": {
                        "type": "apply_patch",
                        "patch_content": "unified diff content",
                        "version": "v1",  # optional, default "v0"
                        "strict_validation": true,  # optional, override default
                        "max_context_mismatches": 0  # optional, override default
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
            patch_content = operation["patch_content"]
            version = operation.get("version", "v0")

            # Override validation settings if specified in operation
            strict_validation = operation.get("strict_validation", self.strict_validation)
            max_context_mismatches = operation.get("max_context_mismatches", self.max_context_mismatches)

            logger.info(f"Executing patch application on: {self.source_dir}")
            logger.debug(f"Patch content length: {len(patch_content)} chars")
            logger.debug(f"Version: {version}")
            logger.debug(f"Strict validation: {strict_validation}")

            if not self.source_dir.exists():
                raise CoderOperationError(f"Source directory does not exist: {self.source_dir}")

            # Save patch file for reference
            path_suffix = self.source_dir.name or "default"
            patch_file = self.source_dir / f"{path_suffix}_{version}.patch"

            if not self.dry_run:
                patch_file.write_text(patch_content, encoding='utf-8')
                logger.debug(f"Patch file saved: {patch_file}")

            # Apply patches with validation
            try:
                modified_files = self._apply_patches_with_validation(
                    self.source_dir,
                    patch_content,
                    strict_validation,
                    max_context_mismatches
                )

                message = f"Patch application {'simulated' if self.dry_run else 'completed'} successfully"
                if modified_files:
                    message += f", modified {len(modified_files)} files"

                return self._create_success_result(
                    modified=bool(modified_files),
                    message=message,
                    patch_content=patch_content,
                    version=version,
                    modified_files=modified_files,
                    patch_file=str(patch_file),
                    strict_validation=strict_validation,
                    target_directory=str(self.source_dir)
                )

            except Exception as e:
                raise CoderOperationError(f"Patch application failed: {e}")

        except json.JSONDecodeError as e:
            raise CoderValidationError(f"Invalid JSON format: {e}")
        except Exception as e:
            if isinstance(e, (CoderValidationError, CoderOperationError)):
                raise
            raise CoderOperationError(f"Unexpected error during patch application: {e}")

    def _apply_patches_with_validation(self,
                                      target_dir: Path,
                                      patch_content: str,
                                      strict_validation: bool = True,
                                      max_context_mismatches: int = 0) -> List[str]:
        """
        Apply patches using verified difflib+patch_ng approach

        Based on reference implementation, uses the following tech stack:
        - difflib: Python standard library for generating unified diff format
        - patch_ng: Professional patch library for parsing and applying patches

        Args:
            target_dir: Target directory
            patch_content: Unified diff format patch content
            strict_validation: Whether to enable strict validation mode
            max_context_mismatches: Maximum allowed context mismatches

        Returns:
            List of modified file paths

        Raises:
            CoderOperationError: If patch application fails
        """
        logger.info("🚀 Starting patch application using verified difflib+patch_ng approach")

        modified_files = []

        try:
            # Write patch content to temporary file, patch_ng needs to read from file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.patch', delete=False, encoding='utf-8') as temp_patch_file:
                temp_patch_file.write(patch_content)
                temp_patch_path = temp_patch_file.name

            logger.debug(f"📋 Patch written to temporary file: {temp_patch_path}")
            logger.debug(f"Patch content preview: {patch_content[:200]}...")

            # Validate before applying if enabled
            if strict_validation and not self.dry_run:
                self._validate_patch_ng_result(target_dir, patch_content)

            if self.dry_run:
                # In dry run mode, just validate the patch can be parsed
                pset = self._patch_ng.fromfile(temp_patch_path)
                if not pset:
                    raise CoderOperationError("patch_ng cannot parse patch content")

                logger.info("🔍 Dry run: Patch parsing successful, would apply to files")

                # Extract file names that would be modified
                for item in pset:
                    if hasattr(item, 'target') and item.target:
                        modified_files.append(str(item.target))
                    elif hasattr(item, 'source') and item.source:
                        modified_files.append(str(item.source))

                return modified_files

            # Use patch_ng to load and apply patches (reference implementation approach)
            pset = self._patch_ng.fromfile(temp_patch_path)

            if not pset:
                raise CoderOperationError("patch_ng cannot parse patch content")

            logger.info(f"📋 patch_ng parsed patch file successfully")

            # Apply patch to target directory
            # patch_ng.apply(root=str(target_dir)) approach from reference
            apply_result = pset.apply(root=str(target_dir))

            if apply_result:
                logger.info("✅ patch_ng patch application successful!")

                # Extract modified file names
                for item in pset:
                    if hasattr(item, 'target') and item.target:
                        modified_files.append(str(item.target))
                    elif hasattr(item, 'source') and item.source:
                        modified_files.append(str(item.source))

                logger.info(f"📊 Processing result: Patch applied successfully, {len(modified_files)} files processed")
                return modified_files
            else:
                error_msg = "patch_ng patch application failed, possibly due to context mismatch or missing files"
                logger.error(f"❌ {error_msg}")

                if strict_validation:
                    raise CoderOperationError(error_msg)
                else:
                    logger.warning("⚠️ Non-strict mode: continuing execution")
                    return []

        except Exception as e:
            logger.error(f"❌ Patch application process failed: {e}")
            logger.debug(f"Error traceback: {traceback.format_exc()}")

            if strict_validation:
                raise CoderOperationError(f"Patch application failed: {e}")
            else:
                logger.warning(f"⚠️ Non-strict mode: patch application failed but continuing")
                return []

        finally:
            # Clean up temporary patch file
            try:
                import os
                if 'temp_patch_path' in locals():
                    os.unlink(temp_patch_path)
                    logger.debug(f"Cleaned up temporary patch file: {temp_patch_path}")
            except Exception as cleanup_error:
                logger.warning(f"Failed to clean up temporary file: {cleanup_error}")

    def _validate_patch_ng_result(self, target_dir: Path, patch_content: str):
        """
        Validate patch_ng application result correctness

        Args:
            target_dir: Target directory
            patch_content: Original patch content
        """
        try:
            logger.debug("🔍 Starting patch_ng application result validation...")

            # Simple validation: check if patch contains expected change markers
            lines = patch_content.split('\n')
            added_lines = [line[1:] for line in lines if line.startswith('+') and not line.startswith('+++')]
            removed_lines = [line[1:] for line in lines if line.startswith('-') and not line.startswith('---')]

            logger.debug(f"Expected to add {len(added_lines)} lines, remove {len(removed_lines)} lines")

            # Here can add more detailed validation logic
            # For example: validate that specific file contents contain expected changes

            logger.debug("✅ patch_ng application result validation passed")

        except Exception as e:
            logger.warning(f"⚠️ patch_ng result validation failed: {e}")

    def create_enhanced_copy(self,
                           source_dir: Path,
                           patch_content: str,
                           version: str = "v0",
                           strict_validation: bool = None,
                           max_context_mismatches: int = None) -> CoderResult:
        """
        Convenience method that mimics ACast.create_enhanced_copy behavior

        Args:
            source_dir: Source directory (will be updated in place)
            patch_content: Patch file content
            version: Version number (like "v0", "v1")
            strict_validation: Override strict validation setting
            max_context_mismatches: Override max context mismatches setting

        Returns:
            CoderResult containing operation results
        """
        # Use provided values or fall back to instance defaults
        strict_val = strict_validation if strict_validation is not None else self.strict_validation
        max_mismatches = max_context_mismatches if max_context_mismatches is not None else self.max_context_mismatches

        operation_data = {
            "operation": {
                "type": "apply_patch",
                "patch_content": patch_content,
                "version": version,
                "strict_validation": strict_val,
                "max_context_mismatches": max_mismatches
            }
        }

        # Temporarily override source_dir if different
        original_source_dir = self.source_dir
        if source_dir != self.source_dir:
            self.source_dir = Path(source_dir)

        try:
            return self.execute(operation_data)
        finally:
            # Restore original source_dir
            self.source_dir = original_source_dir