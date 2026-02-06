"""
Code Modification Operations - Coder Implementations

This package contains various coder implementations for code modification operations,
following design patterns inspired by the aider project.

Available Coders:
- BaseCoder: Abstract base class for all coders
- SearchReplaceCoder: Handles search-and-replace operations with exact matching
- DmpCoder: Handles patch application using difflib+patch_ng
- OpCoder: Handles JSON operations deployment via patch conversion
"""

from .base_coder import BaseCoder, CoderResult, CoderValidationError, CoderOperationError
from .dmp_coder import DmpCoder
from .op_coder import OpCoder
from .search_replace_coder import SearchReplaceCoder

__all__ = [
    'BaseCoder',
    'CoderResult',
    'CoderValidationError',
    'CoderOperationError',
    'SearchReplaceCoder',
    'DmpCoder',
    'OpCoder'
]