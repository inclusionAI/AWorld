# coding: utf-8

import base64
import json
import os
import re
from io import BytesIO
from typing import Any, Dict, List
from urllib.parse import urlparse

import requests
from PIL import Image


def encode_image(image_url: str, with_header: bool = True) -> str:
    """Encode image to base64 format

    Args:
        image_url (str): URL or local file path of the image
        with_header (bool, optional): Whether to include MIME type header. Defaults to True.

    Returns:
        str: Base64 encoded image string, with MIME type prefix if with_header is True

    Raises:
        ValueError: When image URL is empty or image format is not supported
    """
    # extension: MIME type
    mime_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }

    if not image_url:
        raise ValueError("Image URL cannot be empty")

    if not any(image_url.endswith(ext) for ext in mime_types):
        raise ValueError(
            f"Unsupported image format. Supported formats: {', '.join(mime_types)}"
        )
    parsed_url = urlparse(image_url)
    is_url = all([parsed_url.scheme, parsed_url.netloc])
    if not is_url:
        image_base64 = encode_image_from_file(image_url)
    else:
        image_base64 = encode_image_from_url(image_url)

    mime_type = mime_types.get(os.path.splitext(image_url)[1], "image/jpeg")
    final_image = (
        f"data:{mime_type};base64,{image_base64}" if with_header else image_base64
    )
    return final_image


def handle_llm_response(response_content: str, result_key: str) -> str:
    """Process LLM response uniformly

    Args:
        response_content: Raw response content from LLM
        result_key: Key name to extract from JSON

    Returns:
        str: Extracted result content

    Raises:
        ValueError: When response is empty or result key doesn't exist
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
    return json.dumps(result)


def create_image_content(prompt: str, image_base64: str) -> List[Dict[str, Any]]:
    """Create uniform image format for querying llm."""
    return [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": image_base64}},
    ]


def encode_image_from_url(image_url: str) -> str:
    """Fetch an image from URL and encode it to base64

    Args:
        image_url: URL of the image

    Returns:
        str: base64 encoded image string

    Raises:
        requests.RequestException: When failed to fetch the image
        PIL.UnidentifiedImageError: When image format cannot be identified
    """
    response = requests.get(image_url, timeout=10)
    image = Image.open(BytesIO(response.content))

    max_size = 1024
    if max(image.size) > max_size:
        ratio = max_size / max(image.size)
        new_size = (int(image.size[0] * ratio), int(image.size[1] * ratio))
        image = image.resize(new_size, Image.Resampling.LANCZOS)

    buffered = BytesIO()
    image_format = image.format if image.format else "JPEG"
    image.save(buffered, format=image_format)
    img_str = base64.b64encode(buffered.getvalue()).decode()
    return img_str


def encode_image_from_file(image_path):
    """Read image from local file and encode to base64 format."""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode()
