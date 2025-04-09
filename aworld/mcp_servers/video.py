import base64
from fileinput import filename
from typing import Any, Dict, List

import cv2
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

VIDEO_ANALYZE = (
    "Input is a sequence of video frames. Given user's task: {task}, "
    "analyze the video content following these steps:\n"
    "1. Temporal sequence understanding\n"
    "2. Motion and action analysis\n"
    "3. Scene context interpretation\n"
    "4. Object and person tracking\n"
    "Return a json string with the following format: "
    '{{"video_analysis_result": "analysis result given task and video frames"}}'
)

VIDEO_EXTRACT_SUBTITLES = (
    "Input is a sequence of video frames. "
    "Extract all subtitles (if present) in the video. "
    "Return a json string with the following format: "
    '{{"video_subtitles": "extracted subtitles from video"}}'
)

VIDEO_SUMMARIZE = (
    "Input is a sequence of video frames. "
    "Summarize the main content of the video. "
    "Include key points, main topics, and important visual elements. "
    "Return a json string with the following format: "
    '{{"video_summary": "concise summary of the video content"}}'
)


def get_video_frames(video_path: str, sample_rate: int = 2) -> List[Dict[str, Any]]:
    """Get frames from video with given sample rate"""
    video = cv2.VideoCapture(video_path)
    fps = video.get(cv2.CAP_PROP_FPS)
    frame_count = int(video.get(cv2.CAP_PROP_FRAME_COUNT))
    frames = []

    for i in range(0, frame_count, int(fps / sample_rate)):
        video.set(cv2.CAP_PROP_POS_FRAMES, i)
        ret, frame = video.read()
        if not ret:
            break
        _, buffer = cv2.imencode(".jpg", frame)
        frame_data = base64.b64encode(buffer).decode("utf-8")
        frames.append({"data": frame_data, "time": i / fps})
    video.release()
    return frames


def create_video_content(
    prompt: str, video_frames: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Create uniform video format for querying llm."""
    content = [{"type": "text", "text": prompt}]
    content.extend(
        [
            {"type": "image_url", "image_url": {"url": frame["data"]}}
            for frame in video_frames
        ]
    )
    return content


def mcpanalyze(
    video_url: str = Field(description="The input video in given filepath or url."),
    question: str = Field(description="The question to analyze."),
    sample_rate: int = Field(default=2, description="Sample n frames per second."),
) -> str:
    """analyze the video content by the given question."""
    llm = get_llm_model(llm_config)

    inputs = []
    try:
        video_frames = get_video_frames(video_url, sample_rate)
        content = create_video_content(
            VIDEO_ANALYZE.format(task=question), video_frames
        )
        inputs.append({"role": "user", "content": content})

        response = llm.chat.completions.create(
            messages=inputs,
            model="gpt-4o",
            **{"temperature": 0.7},
        )
        video_analysis_result = handle_llm_response(
            response.choices[0].message.content, "video_analysis_result"
        )
    except (ValueError, IOError, RuntimeError) as e:
        video_analysis_result = ""
        logger.error(f"video_analysis-Execute error: {str(e)}")

    logger.info(
        f"---get_analysis_by_video-video_analysis_result:{video_analysis_result}"
    )
    return video_analysis_result


def mcpextractsubtitles(
    video_url: str = Field(description="The input video in given filepath or url."),
    sample_rate: int = Field(default=2, description="Sample n frames per second."),
) -> str:
    """extract subtitles from the video."""
    llm = get_llm_model(llm_config)

    inputs = []
    try:
        video_frames = get_video_frames(video_url, sample_rate)
        content = create_video_content(VIDEO_EXTRACT_SUBTITLES, video_frames)
        inputs.append({"role": "user", "content": content})

        response = llm.chat.completions.create(
            messages=inputs,
            model="gpt-4o",
            **{"temperature": 0.7},
        )
        video_subtitles = handle_llm_response(
            response.choices[0].message.content, "video_subtitles"
        )
    except (ValueError, IOError, RuntimeError) as e:
        video_subtitles = ""
        logger.error(f"video_subtitles-Execute error: {str(e)}")

    logger.info(
        f"---get_subtitles_from_video-video_subtitles:{video_subtitles}"
    )  # 使用 logger.info
    return video_subtitles


def mcpsummarize(
    video_url: str = Field(description="The input video in given filepath or url."),
    sample_rate: int = Field(default=2, description="Sample n frames per second."),
) -> str:
    """summarize the main content of the video."""
    llm = get_llm_model(llm_config)

    inputs = []
    try:
        video_frames = get_video_frames(video_url, sample_rate)
        content = create_video_content(VIDEO_SUMMARIZE, video_frames)
        inputs.append({"role": "user", "content": content})

        response = llm.chat.completions.create(
            messages=inputs,
            model="gpt-4o",
            **{"temperature": 0.7},
        )
        video_summary = handle_llm_response(
            response.choices[0].message.content, "video_summary"
        )
    except (ValueError, IOError, RuntimeError) as e:
        video_summary = ""
        logger.error(f"video_summary-Execute error: {str(e)}")

    logger.info(
        f"---get_summary_from_video-video_summary:{video_summary}"
    )  # 使用 logger.info
    return video_summary


if __name__ == "__main__":
    run_mcp_server(
        "Video Server", funcs=[mcpanalyze, mcpextractsubtitles, mcpsummarize], port=3333
    )
