# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import base64
import json
import os
import re
from typing import Any, Dict, List
from urllib.parse import urlparse

import requests


def encode_video(video_url: str, with_header: bool = True) -> str:
    """Encode video to base64 format

    Args:
        video_url (str): URL or local file path of the video
        with_header (bool, optional): Whether to include MIME type header. Defaults to True.

    Returns:
        str: Base64 encoded video string, with MIME type prefix if with_header is True

    Raises:
        ValueError: When video URL is empty or video format is not supported
    """
    # extension: MIME type
    mime_types = {
        ".mp4": "video/mp4",
        ".avi": "video/x-msvideo",
        ".mov": "video/quicktime",
        ".wmv": "video/x-ms-wmv",
        ".mkv": "video/x-matroska",
        ".webm": "video/webm",
    }

    if not video_url:
        raise ValueError("Video URL cannot be empty")

    if not any(video_url.endswith(ext) for ext in mime_types):
        raise ValueError(
            f"Unsupported video format. Supported formats: {', '.join(mime_types)}"
        )
    
    parsed_url = urlparse(video_url)
    is_url = all([parsed_url.scheme, parsed_url.netloc])
    if not is_url:
        video_base64 = encode_video_from_file(video_url)
    else:
        video_base64 = encode_video_from_url(video_url)

    ext = os.path.splitext(video_url)[1].lower()
    mime_type = mime_types.get(ext, "video/mp4")
    final_video = (
        f"data:{mime_type};base64,{video_base64}" if with_header else video_base64
    )
    return final_video


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
    return result


def create_video_content(prompt: str, video_base64: str) -> List[Dict[str, Any]]:
    """Create uniform video content format for querying llm."""
    return [
        {"type": "text", "text": prompt},
        {"type": "video", "video": {"url": video_base64}}
    ]


def encode_video_from_url(video_url: str) -> str:
    """Fetch a video from URL and encode it to base64

    Args:
        video_url: URL of the video

    Returns:
        str: base64 encoded video string

    Raises:
        requests.RequestException: When failed to fetch the video
    """
    response = requests.get(video_url, timeout=30)  # 视频可能较大，增加超时时间
    video_bytes = response.content
    video_base64 = base64.b64encode(video_bytes).decode()
    return video_base64


def encode_video_from_file(video_path: str) -> str:
    """Read video from local file and encode to base64 format."""
    with open(video_path, "rb") as video_file:
        return base64.b64encode(video_file.read()).decode()