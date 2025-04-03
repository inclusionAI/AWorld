# coding: utf-8
# Copyright (c) 2025 inclusionAI.

# pylint: disable=all

import base64
import json
import re
from typing import List

from cv2 import (
    CAP_PROP_FPS,
    CAP_PROP_FRAME_COUNT,
    CAP_PROP_POS_FRAMES,
    VideoCapture,
    imencode,
)


def get_video_duration(video_path: str) -> float:
    """
    Get the duration of a video file.
    Args:
        video_path (str): The path to the video file.
    Returns:
        float: The duration of the video in seconds.
    """
    video: VideoCapture = VideoCapture(video_path)
    fps = video.get(CAP_PROP_FPS)
    frame_count = int(video.get(CAP_PROP_FRAME_COUNT))
    duration = frame_count / fps
    video.release()
    return duration


def get_video_frames(video_path: str, sample_rate: int = 2) -> List[str]:
    """
    Get the frames of a video file.
    Args:
        video_path (str): The path to the video file.
        sample_rate (int): Sample n frames per second.
    Returns:
        List[str]: The frames of the video.
    """
    video: VideoCapture = VideoCapture(video_path)
    fps = video.get(CAP_PROP_FPS)
    frame_count = int(video.get(CAP_PROP_FRAME_COUNT))
    frames = []
    for i in range(0, frame_count, int(fps / sample_rate)):
        video.set(CAP_PROP_POS_FRAMES, i)
        ret, frame = video.read()
        if not ret:
            break
        _, buffer = imencode(".jpg", frame)
        frame_data = base64.b64encode(buffer).decode("utf-8")
        frames.append(f"data:image/jpeg;base64,{frame_data}")
    video.release()
    return frames


def create_video_content(prompt: str, frames: List[str]):
    """
    Create the content for the video.
    Args:
        prompt (str): The prompt for the video.
        frames (List[Dict[str, Any]]): The base64 encoded video.
    Returns:
        str: The content for the video.
    """
    content = [
        {
            "type": "text",
            "text": f"The video is as follows. \n{prompt}",
        }
    ]
    for frame in frames:
        frame_content = {"type": "image_url", "image_url": {"url": frame}}
        content.append(frame_content)
    return content


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
