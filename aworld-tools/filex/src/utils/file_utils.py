"""File validation utilities."""

import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


async def validate_file(file_path: Optional[Path]) -> Dict[str, Any]:
    """Validate a file path, existence, and size.

    Returns validation status, an optional error message, and the file size.
    """
    logger.debug(
       f"file_utils.validate_file called | file_path={file_path}"
    )

    # Reject an empty path.
    if not file_path:
        logger.warning(
           f" file_utils.validate_file failed | file_path is None"
        )
        return {
            "is_valid": False,
            "error_message": "File path is empty",
            "file_size": None,
        }

    # Check file existence and size.
    try:
        if not file_path.exists():
            logger.warning(
               f" file_utils.validate_file failed | file_path={file_path} file not exists"
            )
            return {
                "is_valid": False,
                "error_message": "File does not exist",
                "file_size": None,
            }

        file_size = file_path.stat().st_size
        if file_size == 0:
            logger.warning(
               f" file_utils.validate_file failed | file_path={file_path} file is empty"
            )
            return {
                "is_valid": False,
                "error_message": "File is empty",
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
            "error_message":f"Unable to read file information: {str(e)}",
            "file_size": None,
        }


async def verify_file_type(file_path: Path, expected_type: str) -> bool:
    """Verify the expected type from file signatures instead of the suffix."""
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
            header = f.read(64)  # Read enough bytes to identify container formats.

        if len(header) == 0:
            logger.warning(
               f" file_utils.verify_file_type failed | "
               f"file_path={file_path} file is empty"
            )
            return False

        # Define file signatures. Some types share the same container signature.
        file_type_signatures = {
            'pdf': [
                b'%PDF',
            ],
            'docx': [
                b'PK\x03\x04',  # ZIP container
            ],
            'doc': [
                b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1',  # Legacy MS Office OLE2 container
            ],
            'xlsx': [
                b'PK\x03\x04',  # ZIP container
            ],
            'xls': [
                b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1',  # OLE2 container
            ],
            'pptx': [
                b'PK\x03\x04',  # ZIP container
            ],
            'ppt': [
                b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1',  # OLE2 container
            ],
            'csv': [
                # CSV has no stable signature, so strict validation is skipped.
                None,
            ],
            'txt': [
                # Plain text has no stable signature.
                None,
            ],
            'md': [
                # Markdown has no stable signature.
                None,
            ],
            'markdown': [
                # Markdown has no stable signature.
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

        # Accept formats that have no stable file signature.
        if expected_signatures is None or None in expected_signatures:
            logger.debug(
               f" file_utils.verify_file_type no signature check | "
               f"file_path={file_path} expected_type={expected_type}"
            )
            return True

        # Match the header against every accepted signature.
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
        # Treat read failures as a type mismatch.
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
