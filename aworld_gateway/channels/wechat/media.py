from __future__ import annotations

import base64
import mimetypes
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

try:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except ImportError:  # pragma: no cover - optional dependency boundary
    default_backend = None  # type: ignore[assignment]
    Cipher = None  # type: ignore[assignment]
    algorithms = None  # type: ignore[assignment]
    modes = None  # type: ignore[assignment]

ITEM_TEXT = 1
ITEM_IMAGE = 2
ITEM_VOICE = 3
ITEM_FILE = 4
ITEM_VIDEO = 5

MEDIA_IMAGE = 1
MEDIA_VIDEO = 2
MEDIA_FILE = 3
MEDIA_VOICE = 4

DEFAULT_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"

MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")
INLINE_CODE_MEDIA_REF_RE = re.compile(r"`((?:attachment://|file://|MEDIA:)[^\s`]+)`")
PLAIN_MEDIA_REF_RE = re.compile(r"((?:attachment://|file://|MEDIA:)[^\s<>)]+)")

_WECHAT_CDN_ALLOWLIST: frozenset[str] = frozenset(
    {
        "novac2c.cdn.weixin.qq.com",
        "ilinkai.weixin.qq.com",
        "wx.qlogo.cn",
        "thirdwx.qlogo.cn",
        "res.wx.qq.com",
        "mmbiz.qpic.cn",
        "mmbiz.qlogo.cn",
    }
)


@dataclass(frozen=True)
class OutboundMediaRequest:
    path: Path
    force_file_attachment: bool = False
    media_kind_override: str | None = None


def sanitize_filename(file_name: str) -> str:
    name = os.path.basename(file_name or "").strip() or "attachment"
    safe = "".join(ch if (ch.isalnum() or ch in {"-", "_", "."}) else "_" for ch in name)
    return safe or "attachment"


def mime_from_filename(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


def build_image_data_url(payload: bytes, mime_type: str) -> str | None:
    normalized = str(mime_type or "").strip().lower()
    if not normalized.startswith("image/"):
        return None
    if not payload:
        return None
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{normalized};base64,{encoded}"


def infer_media_kind(path: Path, *, force_file_attachment: bool = False) -> str:
    if force_file_attachment:
        return "file"
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if path.suffix.lower() == ".silk":
        return "voice"
    return "file"


def build_attachment_prompt(attachments: list[dict[str, Any]]) -> str:
    if not attachments:
        return ""
    lines = ["Attachments:"]
    for attachment in attachments:
        kind = str(attachment.get("type") or "file").strip() or "file"
        path = str(attachment.get("path") or "").strip()
        if path:
            lines.append(f"- {kind}: {path}")
    return "\n".join(lines)


def extract_local_file_path(raw_reference: str) -> Path | None:
    candidate = raw_reference.strip().strip("<>").strip("'").strip('"').strip("`")
    if not candidate:
        return None
    candidate = candidate.replace("\\ ", " ")
    if candidate.startswith("file://"):
        candidate = candidate[len("file://") :]
    elif candidate.startswith("attachment://"):
        candidate = candidate[len("attachment://") :]
    elif candidate.startswith("MEDIA:"):
        candidate = candidate[len("MEDIA:") :]
    candidate = unquote(candidate).strip()
    if not candidate:
        return None
    path = Path(candidate).expanduser()
    if not path.is_absolute() or not path.exists() or not path.is_file():
        return None
    return path


def extract_outbound_media_requests(content: str) -> tuple[str, list[OutboundMediaRequest]]:
    if not content:
        return content, []

    result = content
    requests: list[OutboundMediaRequest] = []

    for match in list(MARKDOWN_IMAGE_RE.finditer(result)):
        full_match, _alt_text, raw_url = match.group(0), match.group(1), match.group(2)
        local_path = extract_local_file_path(raw_url)
        if local_path is None:
            continue
        requests.append(OutboundMediaRequest(path=local_path, force_file_attachment=False))
        result = result.replace(full_match, "", 1)

    for match in list(MARKDOWN_LINK_RE.finditer(result)):
        full_match, _link_text, raw_url = match.group(0), match.group(1), match.group(2)
        local_path = extract_local_file_path(raw_url)
        if local_path is None:
            continue
        requests.append(OutboundMediaRequest(path=local_path, force_file_attachment=True))
        result = result.replace(full_match, "", 1)

    def _replace_inline_code_reference(match: re.Match[str]) -> str:
        local_path = extract_local_file_path(match.group(1))
        if local_path is None:
            return match.group(0)
        requests.append(OutboundMediaRequest(path=local_path, force_file_attachment=False))
        return ""

    def _replace_plain_reference(match: re.Match[str]) -> str:
        local_path = extract_local_file_path(match.group(1))
        if local_path is None:
            return match.group(0)
        requests.append(OutboundMediaRequest(path=local_path, force_file_attachment=False))
        return ""

    result = INLINE_CODE_MEDIA_REF_RE.sub(_replace_inline_code_reference, result)
    result = PLAIN_MEDIA_REF_RE.sub(_replace_plain_reference, result)
    return cleanup_processed_text(result), requests


def cleanup_processed_text(content: str) -> str:
    lines = [line.rstrip() for line in content.splitlines()]
    result: list[str] = []
    blank_run = 0
    for line in lines:
        if not line.strip():
            blank_run += 1
            if blank_run > 1:
                continue
            result.append("")
            continue
        blank_run = 0
        result.append(line.strip())
    return "\n".join(result).strip()


def cdn_download_url(cdn_base_url: str, encrypted_query_param: str) -> str:
    return f"{cdn_base_url.rstrip('/')}/download?encrypted_query_param={quote(encrypted_query_param, safe='')}"


def cdn_upload_url(cdn_base_url: str, upload_param: str, filekey: str) -> str:
    return (
        f"{cdn_base_url.rstrip('/')}/upload"
        f"?encrypted_query_param={quote(upload_param, safe='')}"
        f"&filekey={quote(filekey, safe='')}"
    )


def assert_wechat_cdn_url(url: str) -> None:
    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        host = parsed.hostname or ""
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Unparseable media URL: {url!r}") from exc
    if scheme not in {"http", "https"}:
        raise ValueError(f"Media URL has disallowed scheme {scheme!r}.")
    if host not in _WECHAT_CDN_ALLOWLIST:
        raise ValueError(f"Media URL host {host!r} is not in the WeChat CDN allowlist.")


def parse_aes_key(aes_key_b64: str) -> bytes:
    decoded = base64.b64decode(aes_key_b64)
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32:
        text = decoded.decode("ascii", errors="ignore")
        if text and all(ch in "0123456789abcdefABCDEF" for ch in text):
            return bytes.fromhex(text)
    raise ValueError(f"unexpected aes_key format ({len(decoded)} decoded bytes)")


def pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def aes128_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    if Cipher is None or algorithms is None or modes is None or default_backend is None:
        raise RuntimeError("cryptography is required for WeChat media encryption")
    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    encryptor = cipher.encryptor()
    return encryptor.update(pkcs7_pad(plaintext)) + encryptor.finalize()


def aes128_ecb_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    if Cipher is None or algorithms is None or modes is None or default_backend is None:
        raise RuntimeError("cryptography is required for WeChat media decryption")
    cipher = Cipher(algorithms.AES(key), modes.ECB(), backend=default_backend())
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    if not padded:
        return padded
    pad_len = padded[-1]
    if 1 <= pad_len <= 16 and padded.endswith(bytes([pad_len]) * pad_len):
        return padded[:-pad_len]
    return padded


def aes_padded_size(size: int) -> int:
    return ((size + 1 + 15) // 16) * 16


def build_outbound_media_item(
    *,
    path: Path,
    encrypted_query_param: str,
    aes_key_for_api: str,
    ciphertext_size: int,
    plaintext_size: int,
    rawfilemd5: str,
    force_file_attachment: bool = False,
    media_kind_override: str | None = None,
) -> tuple[int, dict[str, Any]]:
    kind = str(media_kind_override or "").strip().lower() or infer_media_kind(
        path,
        force_file_attachment=force_file_attachment,
    )
    if kind == "image":
        return MEDIA_IMAGE, {
            "type": ITEM_IMAGE,
            "image_item": {
                "media": {
                    "encrypt_query_param": encrypted_query_param,
                    "aes_key": aes_key_for_api,
                    "encrypt_type": 1,
                },
                "mid_size": ciphertext_size,
            },
        }
    if kind == "video":
        return MEDIA_VIDEO, {
            "type": ITEM_VIDEO,
            "video_item": {
                "media": {
                    "encrypt_query_param": encrypted_query_param,
                    "aes_key": aes_key_for_api,
                    "encrypt_type": 1,
                },
                "video_size": ciphertext_size,
                "play_length": 0,
                "video_md5": rawfilemd5,
            },
        }
    if kind == "voice":
        return MEDIA_VOICE, {
            "type": ITEM_VOICE,
            "voice_item": {
                "media": {
                    "encrypt_query_param": encrypted_query_param,
                    "aes_key": aes_key_for_api,
                    "encrypt_type": 1,
                },
                "encode_type": 6,
                "bits_per_sample": 16,
                "sample_rate": 24000,
                "playtime": 0,
            },
        }
    return MEDIA_FILE, {
        "type": ITEM_FILE,
        "file_item": {
            "media": {
                "encrypt_query_param": encrypted_query_param,
                "aes_key": aes_key_for_api,
                "encrypt_type": 1,
            },
            "file_name": path.name,
            "len": str(plaintext_size),
        },
    }
