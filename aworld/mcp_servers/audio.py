import base64
import json
import os
from urllib.parse import urlparse

from pydantic import Field

from aworld.logs.util import logger
from aworld.mcp_servers.utils import (
    get_current_filename_without_extension,
    read_llm_config_from_yaml,
    run_mcp_server,
)
from aworld.models.llm import get_llm_model

filename = get_current_filename_without_extension()
llm_config = read_llm_config_from_yaml(f"{filename}_analysis_tool.yaml")

AUDIO_TRANSCRIBE = (
    "Input is a base64 encoded audio. Transcribe the audio content. "
    "Return a json string with the following format: "
    '{{"audio_text": "transcribed text from audio"}}'
)


def encode_audio_from_url(audio_url: str) -> str:
    """Fetch an audio from URL and encode it to base64"""
    response = requests.get(audio_url, timeout=10)
    audio_bytes = response.content
    audio_base64 = base64.b64encode(audio_bytes).decode()
    return audio_base64


def encode_audio_from_file(audio_path: str) -> str:
    """Read audio from local file and encode to base64 format."""
    with open(audio_path, "rb") as audio_file:
        return base64.b64encode(audio_file.read()).decode()


def encode_audio(audio_url: str, with_header: bool = True) -> str:
    """Encode audio to base64 format"""
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


def mcptranscribe(
    audio_urls: str = Field(
        description="The input audio in given a list of filepaths or urls."
    ),
) -> str:
    """transcribe the given audio in a list of filepaths or urls."""
    llm = get_llm_model(llm_config)

    transcriptions = []
    try:
        for audio_url in audio_urls:
            with open(audio_url, "rb") as audio_file:
                transcription = llm.audio.transcriptions.create(
                    file=audio_file,
                    model="gpt-4o-transcribe",
                    response_format="text",
                )
                transcriptions.append(transcription)
    except (ValueError, IOError, RuntimeError) as e:
        logger.error(f"audio_transcribe-Execute error: {str(e)}")

    logger.info(f"---get_text_by_transcribe-transcription:{transcriptions}")
    return json.dumps(transcriptions, ensure_ascii=False)


if __name__ == "__main__":
    run_mcp_server("Audio Server", funcs=[mcptranscribe], port=2222)
