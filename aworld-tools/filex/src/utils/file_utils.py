"""
文件工具模块

提供文件相关的工具函数
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


async def validate_file(file_path: Optional[Path]) -> Dict[str, Any]:
    """验证文件的基本信息（路径、存在性、大小）

    参数：
        file_path: 文件路径（可能为None）

    返回：
        字典，包含以下键：
        - is_valid: bool, True表示文件有效，False表示文件无效
        - error_message: Optional[str], 如果文件无效，返回错误信息；如果文件有效，返回None
        - file_size: Optional[int], 如果文件有效，返回文件大小（字节）；如果文件无效，返回None
    """
    logger.debug(
       f"file_utils.validate_file called | file_path={file_path}"
    )

    # 检查文件路径是否为空
    if not file_path:
        logger.warning(
           f" file_utils.validate_file failed | file_path is None"
        )
        return {
            "is_valid": False,
            "error_message": "文件路径为空",
            "file_size": None,
        }

    # 检查文件是否存在和大小
    try:
        if not file_path.exists():
            logger.warning(
               f" file_utils.validate_file failed | file_path={file_path} file not exists"
            )
            return {
                "is_valid": False,
                "error_message": "文件不存在",
                "file_size": None,
            }

        file_size = file_path.stat().st_size
        if file_size == 0:
            logger.warning(
               f" file_utils.validate_file failed | file_path={file_path} file is empty"
            )
            return {
                "is_valid": False,
                "error_message": "文件为空",
                "file_size": 0,
            }

        logger.debug(
           f" file_utils.validate_file success | file_path={file_path} file_size={file_size}"
        )

        return {
            "is_valid": True,
            "error_message": None,
            "file_size": file_size,
        }
    except Exception as e:
        logger.warning(
           f" file_utils.validate_file exception | file_path={file_path} error={str(e)}"
        )
        return {
            "is_valid": False,
            "error_message":f"无法获取文件信息: {str(e)}",
            "file_size": None,
        }


async def verify_file_type(file_path: Path, expected_type: str) -> bool:
    """验证文件类型是否与预期一致（通过文件头判断，不依赖扩展名）

    参数：
        file_path: 文件路径
        expected_type: 预期的文件类型（如 'pdf', 'docx', 'xlsx' 等）

    返回：
        如果文件类型匹配返回True，否则返回False
    """
    logger.debug(
        f"file_utils.verify_file_type called | "
        f"file_path={file_path} expected_type={expected_type}"
    )

    if not file_path.exists():
        logger.warning(
           f" file_utils.verify_file_type failed | "
           f"file_path={file_path} file not exists"
        )
        return False

    try:
        with open(file_path, 'rb') as f:
            header = f.read(64)  # 读取前64字节用于判断容器格式

        if len(header) == 0:
            logger.warning(
               f" file_utils.verify_file_type failed | "
               f"file_path={file_path} file is empty"
            )
            return False

        # 定义文件类型的magic number（文件头特征）
        # 注意：某些类型可能共享相同的magic number（如docx/xlsx/pptx都是ZIP格式）
        file_type_signatures = {
            'pdf': [
                b'%PDF',
            ],
            'docx': [
                b'PK\x03\x04',  # ZIP格式
            ],
            'doc': [
                b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1',  # OLE2格式（MS Office旧格式）
            ],
            'xlsx': [
                b'PK\x03\x04',  # ZIP格式
            ],
            'xls': [
                b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1',  # OLE2格式
            ],
            'pptx': [
                b'PK\x03\x04',  # ZIP格式
            ],
            'ppt': [
                b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1',  # OLE2格式
            ],
            'csv': [
                # CSV没有特定的magic number，需要检查内容
                # 这里不进行严格验证，因为CSV格式太灵活
                None,
            ],
            'txt': [
                # TXT没有特定的magic number，可以是任何文本
                # 这里不进行严格验证
                None,
            ],
            'md': [
                # Markdown没有特定的magic number
                # 这里不进行严格验证
                None,
            ],
            'markdown': [
                # Markdown没有特定的magic number
                # 这里不进行严格验证
                None,
            ],
            'mp3': [
                b'ID3',
                b'\xff\xfb',
                b'\xff\xf3',
                b'\xff\xf2',
            ],
            'wav': [
                b'RIFF',
            ],
            'flac': [
                b'fLaC',
            ],
            'ogg': [
                b'OggS',
            ],
            'opus': [
                b'OggS',
            ],
            'aac': [
                b'\xff\xf1',
                b'\xff\xf9',
            ],
            'm4a': [
                b'ftyp',
            ],
            'mp4': [
                b'ftyp',
            ],
            'mov': [
                b'ftyp',
            ],
            'm4v': [
                b'ftyp',
            ],
            'mkv': [
                b'\x1a\x45\xdf\xa3',
            ],
            'webm': [
                b'\x1a\x45\xdf\xa3',
            ],
            'avi': [
                b'RIFF',
            ],
            'mpeg': [
                b'\x00\x00\x01\xba',
                b'\x00\x00\x01\xb3',
            ],
            'mpg': [
                b'\x00\x00\x01\xba',
                b'\x00\x00\x01\xb3',
            ],
            'png': [
                b'\x89PNG\r\n\x1a\n',
            ],
            'jpg': [
                b'\xff\xd8\xff',
            ],
            'jpeg': [
                b'\xff\xd8\xff',
            ],
            'webp': [
                b'RIFF',
            ],
            'gif': [
                b'GIF87a',
                b'GIF89a',
            ],
            'bmp': [
                b'BM',
            ],
        }

        expected_signatures = file_type_signatures.get(expected_type.lower())

        # 对于没有特定magic number的类型（csv, txt, md），直接返回True
        if expected_signatures is None or None in expected_signatures:
            logger.debug(
               f" file_utils.verify_file_type no signature check | "
               f"file_path={file_path} expected_type={expected_type}"
            )
            return True

        # 检查文件头是否匹配任一签名
        for signature in expected_signatures:
            if _header_matches_signature(header, expected_type.lower(), signature):
                logger.debug(
                   f" file_utils.verify_file_type match | "
                   f"file_path={file_path} expected_type={expected_type}"
                )
                return True

        logger.warning(
           f" file_utils.verify_file_type mismatch | "
           f"file_path={file_path} expected_type={expected_type}"
        )
        return False

    except Exception as e:
        logger.warning(
           f" file_utils.verify_file_type exception | "
           f"file_path={file_path} expected_type={expected_type} error={str(e)}"
        )
        # 如果读取文件失败，返回False
        return False


def _header_matches_signature(header: bytes, expected_type: str, signature: bytes) -> bool:
    if expected_type in {'m4a', 'mp4', 'mov', 'm4v'}:
        return len(header) >= 12 and header[4:8] == b'ftyp'
    if expected_type == 'wav':
        return header.startswith(b'RIFF') and header[8:12] == b'WAVE'
    if expected_type == 'avi':
        return header.startswith(b'RIFF') and header[8:12] == b'AVI '
    if expected_type == 'webp':
        return header.startswith(b'RIFF') and header[8:12] == b'WEBP'
    return header.startswith(signature)
