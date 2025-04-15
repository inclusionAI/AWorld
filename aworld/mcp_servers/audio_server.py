"""
Audio MCP Server

This module provides MCP (Model-Controller-Processor) server functionality for audio processing.
It includes tools for audio transcription and encoding audio files to base64 format.
The server handles both local audio files and remote audio URLs with proper validation
and supports various audio formats.

Main functions:
- encode_audio: Encodes audio files to base64 format
- mcptranscribe: Transcribes audio content using LLM models
"""

import base64
import json
import os
from typing import List

from pydantic import Field

from aworld.logs.util import logger
from aworld.mcp_servers.utils import (
    get_file_from_source,
    get_llm_config_from_os_environ,
    run_mcp_server,
)
from aworld.models.llm import get_llm_model

llm_config = get_llm_config_from_os_environ(
    llm_model_name="gpt-4o-transcribe", server_name="Audio Server"
)

AUDIO_TRANSCRIBE = (
    "Input is a base64 encoded audio. Transcribe the audio content. "
    "Return a json string with the following format: "
    '{{"audio_text": "transcribed text from audio"}}'
)


def encode_audio(audio_source: str, with_header: bool = True) -> str:
    """
    Encode audio to base64 format with robust file handling

    Args:
        audio_source: URL or local file path of the audio
        with_header: Whether to include MIME type header

    Returns:
        str: Base64 encoded audio string, with MIME type prefix if with_header is True

    Raises:
        ValueError: When audio source is invalid or audio format is not supported
        IOError: When audio file cannot be read
    """
    if not audio_source:
        raise ValueError("Audio source cannot be empty")

    try:
        # Get file with validation (only audio files allowed)
        file_path, mime_type, content = get_file_from_source(
            audio_source,
            allowed_mime_prefixes=["audio/"],
            max_size_mb=25.0,  # 25MB limit for audio files
        )

        # Encode to base64
        audio_base64 = base64.b64encode(content).decode()

        # Format with header if requested
        final_audio = (
            f"data:{mime_type};base64,{audio_base64}" if with_header else audio_base64
        )

        # Clean up temporary file if it was created for a URL
        if file_path != os.path.abspath(audio_source) and os.path.exists(file_path):
            os.unlink(file_path)

        return final_audio

    except Exception as e:
        logger.error(
            f"Error encoding audio from {audio_source}: {traceback.format_exc()}"
        )
        raise


def mcptranscribeaudio(
    audio_urls: List[str] = Field(
        description="The input audio in given a list of filepaths or urls."
    ),
) -> str:
    """
    Transcribe the given audio in a list of filepaths or urls.

    Args:
        audio_urls: List of audio file paths or URLs

    Returns:
        str: JSON string containing transcriptions
    """
    llm = get_llm_model(llm_config)
    real_llm = llm.provider

    transcriptions = []
    for audio_url in audio_urls:
        try:
            # Get file with validation (only audio files allowed)
            file_path, _, _ = get_file_from_source(
                audio_url, allowed_mime_prefixes=["audio/"], max_size_mb=25.0
            )

            # Use the file for transcription
            with open(file_path, "rb") as audio_file:
                transcription = real_llm.audio.transcriptions.create(
                    file=audio_file,
                    model="gpt-4o-transcribe",
                    response_format="text",
                )
                transcriptions.append(transcription)

            # Clean up temporary file if it was created for a URL
            if file_path != os.path.abspath(audio_url) and os.path.exists(file_path):
                os.unlink(file_path)

        except Exception as e:
            logger.error(f"Error transcribing {audio_url}: {traceback.format_exc()}")
            transcriptions.append(f"Error: {str(e)}")

    logger.info(f"---get_text_by_transcribe-transcription:{transcriptions}")
    return json.dumps(transcriptions, ensure_ascii=False)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Launch MCP servers with random port allocation"
    )
    parser.add_argument(
        "--port",
        type=int,
        help=f"Listening to port. Must be specified.",
    )
    args = parser.parse_args()
    run_mcp_server("Audio Server", funcs=[mcptranscribeaudio], port=args.port)
