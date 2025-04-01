# coding: utf-8

import base64
import json
import os
import re
from typing import Any, Dict, List
from urllib.parse import urlparse

import requests
from PIL import Image

from aworld.logs.util import logger


def encode_image(image_url: str, with_header: bool = True) -> str:
    # extension: MIME type
    MIME_TYPE = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }

    if not image_url:
        raise ValueError("Image URL cannot be empty")

    if any(image_url.endswith(ext) for ext in MIME_TYPE.keys()):
        parsed_url = urlparse(image_url)
        is_url = all([parsed_url.scheme, parsed_url.netloc])
        if not is_url:
            image_base64 = encode_image_from_file(image_url)
        else:
            image_base64 = encode_image_from_url(image_url)

        mime_type = MIME_TYPE.get(os.path.splitext(image_url)[1], "image/jpeg")
        final_image = (
            f"data:{mime_type};base64,{image_base64}" if with_header else image_base64
        )
        return final_image
    else:
        raise ValueError(
            f"Unsupported image format. Supported formats: {', '.join(MIME_TYPE.keys())}"
        )


def handle_llm_response(response_content: str, result_key: str) -> str:
    """统一处理 LLM 响应

    Args:
        response_content: LLM 返回的原始响应内容
        result_key: 需要从 JSON 中提取的键名

    Returns:
        str: 提取的结果内容

    Raises:
        ValueError: 当响应为空或结果键不存在时
    """
    if not response_content:
        raise ValueError("No response from llm.")

    json_pattern = r"```json\s*(.*?)\s*```"
    match = re.search(json_pattern, response_content, re.DOTALL)
    if match:
        response_content = match.group(1)

    json_content = json.loads(response_content)
    result = json_content.get(result_key)
    if not result:
        raise ValueError(f"No {result_key} in response.")
    return result


def create_image_content(prompt: str, image_base64: str) -> List[Dict[str, Any]]:
    """创建统一的图像内容格式"""
    return [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": image_base64}},
    ]


def encode_image_from_url(image_url):
    response = requests.get(image_url)
    image = Image.open(BytesIO(response.content))

    max_size = 1024
    if max(image.size) > max_size:
        ratio = max_size / max(image.size)
        new_size = (int(image.size[0] * ratio), int(image.size[1] * ratio))
        image = image.resize(new_size, Image.LANCZOS)

    buffered = BytesIO()
    image_format = image.format if image.format else "JPEG"
    image.save(buffered, format=image_format)
    img_str = base64.b64encode(buffered.getvalue()).decode()
    return img_str


def encode_image_from_file(image_path):
    """从本地文件读取图片并编码为base64"""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode()
