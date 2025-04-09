import base64
import os
from io import BytesIO
from typing import Any, Dict, List
from urllib.parse import urlparse

import requests
from PIL import Image
from pydantic import Field

from aworld.logs.util import logger
from aworld.mcp_servers.utils import (
    get_current_filename_without_extension,
    handle_llm_response,
    read_llm_config_from_yaml,
    run_mcp_server,
)
from aworld.models.llm import get_llm_model

filename = get_current_filename_without_extension()
llm_config = read_llm_config_from_yaml(f"{filename}_analysis_tool.yaml")

IMAGE_OCR = (
    "Input is a base64 encoded image. Read text from image if present. "
    "Return a json string with the following format: "
    '{{"image_text": "text from image"}}'
)

IMAGE_REASONING = (
    "Input is a base64 encoded image. Given user's task: {task}, "
    "solve it following the guide line:\n"
    "1. Careful visual inspection\n"
    "2. Contextual reasoning\n"
    "3. Text transcription where relevant\n"
    "4. Logical deduction from visual evidence\n"
    "Return a json string with the following format: "
    '{{"image_reasoning_result": "reasoning result given task and image"}}'
)


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


def create_image_content(prompt: str, image_base64: str) -> List[Dict[str, Any]]:
    """Create uniform image format for querying llm."""
    return [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": image_base64}},
    ]


def mcpocr(
    image_url: str = Field(description="The input image in given filepath or url."),
) -> str:
    """read text (if present) from the given image in given filepath or url."""
    llm = get_llm_model(llm_config)

    inputs = []
    try:
        image_base64 = encode_image(image_url)
        content = create_image_content(IMAGE_OCR, image_base64)
        inputs.append({"role": "user", "content": content})

        response = llm.chat.completions.create(
            messages=inputs,
            model="gpt-4o",
            **{"temperature": 0.7},
        )
        image_text = handle_llm_response(
            response.choices[0].message.content, "image_text"
        )
    except (ValueError, IOError, RuntimeError) as e:
        logger.error(f"image_text-Execute error: {str(e)}")
        image_text = ""

    logger.info(f"---get_text_by_ocr-image_text:{image_text}")

    return image_text


def mcpreasoning(
    image_url: str = Field(description="The input image in given filepath or url."),
    question: str = Field(description="The question to ask."),
) -> str:
    """solve the question by careful reasoning given the image in given filepath or url."""
    llm = get_llm_model(llm_config)

    inputs = []
    try:
        image_base64 = encode_image(image_url)
        content = create_image_content(
            IMAGE_REASONING.format(task=question), image_base64
        )
        inputs.append({"role": "user", "content": content})

        response = llm.chat.completions.create(
            messages=inputs,
            model="gpt-4o",
            **{"temperature": 0.7},
        )
        image_reasoning_result = handle_llm_response(
            response.choices[0].message.content, "image_reasoning_result"
        )
    except (ValueError, IOError, RuntimeError) as e:
        image_reasoning_result = ""
        logger.error(f"image_reasoning_result-Execute error: {str(e)}")

    logger.info(
        f"---get_reasoning_by_image-image_reasoning_result:{image_reasoning_result}"
    )

    return image_reasoning_result


if __name__ == "__main__":
    run_mcp_server("Image Server", funcs=[mcpocr, mcpreasoning], port=1111)
