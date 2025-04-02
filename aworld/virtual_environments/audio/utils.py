# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import base64
import json
import os
import re
import tempfile
from urllib.parse import urlparse

import requests


def get_audio_filepath_from_url(audio_url: str, save_path: str = None) -> str:
    """Fetch an audio from URL and encode it to base64

    Args:
        audio_url: URL of the audio
        save_path: Path to save the original audio file, if None, use temp directory

    Returns:
        str: audio file path, if not specified, return a temporary path

    Raises:
        requests.RequestException: When failed to fetch the audio
    """
    response = requests.get(audio_url, timeout=60, stream=True)
    audio_bytes = response.content

    if save_path is None:
        file_name = os.path.basename(audio_url)
        save_path = os.path.join(tempfile.gettempdir(), file_name)

    os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
    with open(save_path, "wb") as f:
        f.write(audio_bytes)
    return save_path


def encode_audio(audio_url: str, with_header: bool = True) -> str:
    """Encode audio to base64 format

    Args:
        audio_url (str): URL or local file path of the audio
        with_header (bool, optional): Whether to include MIME type header. Defaults to True.

    Returns:
        str: Base64 encoded audio string, with MIME type prefix if with_header is True

    Raises:
        ValueError: When audio URL is empty or audio format is not supported
    """
    # extension: MIME type
    mime_types = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".m4a": "audio/mp4",
        ".flac": "audio/flac",
    }

    if not audio_url:
        raise ValueError("Audio URL cannot be empty")

    if not any(audio_url.endswith(ext) for ext in mime_types):
        raise ValueError(
            f"Unsupported audio format. Supported formats: {', '.join(mime_types)}"
        )

    parsed_url = urlparse(audio_url)
    is_url = all([parsed_url.scheme, parsed_url.netloc])
    if not is_url:
        audio_base64 = encode_audio_from_file(audio_url)
    else:
        audio_base64 = encode_audio_from_url(audio_url)

    ext = os.path.splitext(audio_url)[1].lower()
    mime_type = mime_types.get(ext, "audio/mpeg")
    final_audio = (
        f"data:{mime_type};base64,{audio_base64}" if with_header else audio_base64
    )
    return final_audio


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


def encode_audio_from_url(audio_url: str) -> str:
    """Fetch an audio from URL and encode it to base64

    Args:
        audio_url: URL of the audio

    Returns:
        str: base64 encoded audio string

    Raises:
        requests.RequestException: When failed to fetch the audio
    """
    response = requests.get(audio_url, timeout=10)
    audio_bytes = response.content
    audio_base64 = base64.b64encode(audio_bytes).decode()
    return audio_base64


def encode_audio_from_file(audio_path: str) -> str:
    """Read audio from local file and encode to base64 format."""
    with open(audio_path, "rb") as audio_file:
        return base64.b64encode(audio_file.read()).decode()
