"""parse_file 专用：文档解析调度与类型校验"""

from .parse_to_path import parse_to_path
from .file_type_utils import verify_file_type

__all__ = ["parse_to_path", "verify_file_type"]

