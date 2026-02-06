"""
Base Coder Interface - Abstract Base Class for Code Modification Operations

This module defines the abstract base class for all code modification operations,
following patterns inspired by the aider project's coder architecture.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, Optional, List, Union

from aworld.logs.util import logger


@dataclass
class CoderResult:
    """
    Standard result object for coder operations

    Provides a consistent interface for all coder operation results,
    enabling uniform error handling and success reporting.
    """
    success: bool
    modified: bool = False
    original_content: str = ""
    new_content: str = ""
    message: str = ""
    error: Optional[str] = None
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class CoderValidationError(Exception):
    """Raised when coder validation fails"""
    pass


class CoderOperationError(Exception):
    """Raised when coder operation fails"""
    pass


class BaseCoder(ABC):
    """
    Abstract base class for code modification operations

    Inspired by aider's coder architecture, this class defines the common
    interface that all specific coder implementations must follow.

    Key design principles:
    - Single Responsibility: Each coder handles one specific type of operation
    - Consistency: All operations return standardized CoderResult objects
    - Validation: Input validation before operation execution
    - Error Handling: Comprehensive error handling and logging
    - Extensibility: Easy to add new coder types
    """

    def __init__(self,
                 source_dir: Path,
                 validation_enabled: bool = True,
                 dry_run: bool = False,
                 backup_enabled: bool = True):
        """
        Initialize base coder with common configuration

        Args:
            source_dir: Directory containing source code to modify
            validation_enabled: Whether to perform input validation
            dry_run: If True, perform validation but don't make actual changes
            backup_enabled: Whether to create backups before modifications
        """
        self.source_dir = Path(source_dir)
        self.validation_enabled = validation_enabled
        self.dry_run = dry_run
        self.backup_enabled = backup_enabled

        # Validate source directory exists
        if not self.source_dir.exists():
            raise CoderValidationError(f"Source directory does not exist: {source_dir}")

    @abstractmethod
    def execute(self, operation_data: Union[str, Dict[str, Any]]) -> CoderResult:
        """
        Execute the coder operation

        Args:
            operation_data: Operation-specific data (JSON string or dict)

        Returns:
            CoderResult object containing operation results

        Raises:
            CoderValidationError: If input validation fails
            CoderOperationError: If operation execution fails
        """
        pass

    @abstractmethod
    def validate_operation_data(self, operation_data: Union[str, Dict[str, Any]]) -> bool:
        """
        Validate operation data before execution

        Args:
            operation_data: Operation data to validate

        Returns:
            True if validation passes

        Raises:
            CoderValidationError: If validation fails
        """
        pass

    def can_handle_operation(self, operation_type: str) -> bool:
        """
        Check if this coder can handle a specific operation type

        Args:
            operation_type: Type of operation to check

        Returns:
            True if this coder can handle the operation type
        """
        return operation_type in self.get_supported_operation_types()

    @abstractmethod
    def get_supported_operation_types(self) -> List[str]:
        """
        Get list of operation types supported by this coder

        Returns:
            List of supported operation type strings
        """
        pass

    def create_backup(self, file_path: Path) -> Optional[Path]:
        """
        Create backup of a file before modification

        Args:
            file_path: Path to file to backup

        Returns:
            Path to backup file if created, None if backup disabled
        """
        if not self.backup_enabled or not file_path.exists():
            return None

        try:
            backup_path = file_path.with_suffix(f"{file_path.suffix}.bak")
            backup_path.write_text(file_path.read_text(encoding='utf-8'), encoding='utf-8')
            logger.debug(f"Created backup: {backup_path}")
            return backup_path
        except Exception as e:
            logger.warning(f"Failed to create backup for {file_path}: {e}")
            return None

    def restore_from_backup(self, file_path: Path) -> bool:
        """
        Restore file from backup

        Args:
            file_path: Path to file to restore

        Returns:
            True if restoration successful
        """
        backup_path = file_path.with_suffix(f"{file_path.suffix}.bak")

        if not backup_path.exists():
            logger.error(f"Backup file not found: {backup_path}")
            return False

        try:
            file_path.write_text(backup_path.read_text(encoding='utf-8'), encoding='utf-8')
            logger.info(f"Restored file from backup: {file_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to restore from backup: {e}")
            return False

    def _create_error_result(self, error_message: str, **kwargs) -> CoderResult:
        """
        Create a standardized error result

        Args:
            error_message: Error description
            **kwargs: Additional metadata

        Returns:
            CoderResult indicating failure
        """
        return CoderResult(
            success=False,
            modified=False,
            error=error_message,
            metadata=kwargs
        )

    def _create_success_result(self,
                              modified: bool = True,
                              message: str = "Operation completed successfully",
                              original_content: str = "",
                              new_content: str = "",
                              **kwargs) -> CoderResult:
        """
        Create a standardized success result

        Args:
            modified: Whether files were modified
            message: Success message
            original_content: Original file content
            new_content: Modified file content
            **kwargs: Additional metadata

        Returns:
            CoderResult indicating success
        """
        return CoderResult(
            success=True,
            modified=modified,
            message=message,
            original_content=original_content,
            new_content=new_content,
            metadata=kwargs
        )

    def __str__(self) -> str:
        """String representation of coder"""
        return f"{self.__class__.__name__}(source_dir={self.source_dir})"

    def __repr__(self) -> str:
        """Detailed representation of coder"""
        return (f"{self.__class__.__name__}("
                f"source_dir={self.source_dir}, "
                f"validation_enabled={self.validation_enabled}, "
                f"dry_run={self.dry_run}, "
                f"backup_enabled={self.backup_enabled})")