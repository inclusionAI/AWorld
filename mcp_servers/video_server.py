import base64
import os
import traceback
from typing import Any, Dict, List

import cv2
from pydantic import Field
from mcp.server.fastmcp import FastMCP
from aworld.logs.util import logger
from mcp_servers.utils import get_file_from_source
from openai import OpenAI

client = OpenAI(
    api_key=os.getenv("LLM_API_KEY"), 
    base_url=os.getenv("LLM_BASE_URL")
)

# Initialize MCP server
mcp = FastMCP("Video Server")


VIDEO_ANALYZE = (
    "Input is a sequence of video frames. Given user's task: {task}. "
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
    '{"video_subtitles": "extracted subtitles from video"}'
)

VIDEO_SUMMARIZE = (
    "Input is a sequence of video frames. "
    "Summarize the main content of the video. "
    "Include key points, main topics, and important visual elements. "
    "Return a json string with the following format: "
    '{"video_summary": "concise summary of the video content"}'
)



def get_video_frames(video_source: str, sample_rate: int = 2, start_time: float = 0, end_time: float = None) -> List[Dict[str, Any]]:
    """
    Get frames from video with given sample rate using robust file handling

    Args:
        video_source: Path or URL to the video file
        sample_rate: Number of frames to sample per second
        start_time: Start time of the video segment in seconds (default: 0)
        end_time: End time of the video segment in seconds (default: None, meaning the end of the video)

    Returns:
        List[Dict[str, Any]]: List of dictionaries containing frame data and timestamp

    Raises:
        ValueError: When video file cannot be opened or is not a valid video
    """
    try:
        # Get file with validation (only video files allowed)
        file_path, _, _ = get_file_from_source(
            video_source,
            allowed_mime_prefixes=["video/"],
            max_size_mb=2500.0,  # 2500MB limit for videos
            type="video",  # Specify type as video to handle video files
        )

        # Open video file
        video = cv2.VideoCapture(file_path)
        if not video.isOpened():
            raise ValueError(f"Could not open video file: {file_path}")

        fps = video.get(cv2.CAP_PROP_FPS)
        frame_count = int(video.get(cv2.CAP_PROP_FRAME_COUNT))
        video_duration = frame_count / fps # 30s

        if end_time is None:
            end_time = video_duration

        if start_time > end_time:
            raise ValueError("Start time cannot be greater than end time.")

        if start_time < 0:
            start_time = 0

        if end_time > video_duration:
            end_time = video_duration

        start_frame = int(start_time * fps)
        end_frame = int(end_time * fps)

        all_frames = []
        frames = []

        # Calculate frame interval based on sample rate
        frame_interval = max(1, int(fps / sample_rate))

        # Set the video capture to the start frame
        video.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

        for i in range(start_frame, end_frame):
            ret, frame = video.read()
            if not ret:
                break

            # Convert frame to JPEG format
            _, buffer = cv2.imencode(".jpg", frame)
            frame_data = base64.b64encode(buffer).decode("utf-8")

            # Add data URL prefix for JPEG image
            frame_data = f"data:image/jpeg;base64,{frame_data}"

            all_frames.append({"data": frame_data, "time": i / fps})

        for i in range(0, len(all_frames), frame_interval):
            frames.append(all_frames[i])

        video.release()

        # Clean up temporary file if it was created for a URL
        if file_path != os.path.abspath(video_source) and os.path.exists(file_path):
            os.unlink(file_path)

        if not frames:
            raise ValueError(f"Could not extract any frames from video: {video_source}")

        return frames

    except Exception as e:
        logger.error(f"Error extracting frames from {video_source}: {str(e)}")
        raise


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


@mcp.tool(description="Analyze the video content by the given question.")
def mcp_analyze_video(
    video_url: str = Field(description="The input video in given filepath or url."),
    question: str = Field(description="The question to analyze."),
    sample_rate: int = Field(default=2, description="Sample n frames per second."),
    start_time: float = Field(default=0, description="Start time of the video segment in seconds."),
    end_time: float = Field(default=None, description="End time of the video segment in seconds."),
) -> str:
    """analyze the video content by the given question."""

    try:
        video_frames = get_video_frames(video_url, sample_rate, start_time, end_time)
        logger.info(f"---len video_frames:{len(video_frames)}")
        interval = 20
        frame_nums = 30
        all_res = []
        for i in range(0, len(video_frames), interval):
            inputs = []
            cur_frames = video_frames[i : i + frame_nums]
            content = create_video_content(
                VIDEO_ANALYZE.format(task=question), cur_frames
            )
            inputs.append({"role": "user", "content": content})
            try:
                response = client.chat.completions.create(
                    model=os.getenv("LLM_MODEL_NAME"),  
                    messages=inputs,
                    temperature=0,
                )
                cur_video_analysis_result = response.choices[0].message.content
            except Exception as e:
                cur_video_analysis_result = ""
            all_res.append(f"result of video part {int(i / interval + 1)}: {cur_video_analysis_result}")
            logger.info(f"result of video part {int(i / interval + 1)}: {cur_video_analysis_result}")
            if i + frame_nums >= len(video_frames):
                break
        video_analysis_result = "\n".join(all_res)

    except (ValueError, IOError, RuntimeError) as e:
        video_analysis_result = ""
        logger.error(f"video_analysis-Execute error: {traceback.format_exc()}")

    logger.info(
        f"---get_analysis_by_video-video_analysis_result:{video_analysis_result}"
    )
    return video_analysis_result


@mcp.tool(description="Extract subtitles from the video.")
def mcp_extract_video_subtitles(
    video_url: str = Field(description="The input video in given filepath or url."),
    sample_rate: int = Field(default=2, description="Sample n frames per second."),
    start_time: float = Field(default=0, description="Start time of the video segment in seconds."),
    end_time: float = Field(default=None, description="End time of the video segment in seconds."),
) -> str:
    """extract subtitles from the video."""
    
    inputs = []
    try:
        video_frames = get_video_frames(video_url, sample_rate, start_time, end_time)
        content = create_video_content(VIDEO_EXTRACT_SUBTITLES, video_frames)
        inputs.append({"role": "user", "content": content})

        response = client.chat.completions.create(
            model=os.getenv("LLM_MODEL_NAME"),  
            messages=inputs,
            temperature=0,
        )
        video_subtitles = response.choices[0].message.content
    except (ValueError, IOError, RuntimeError) as e:
        video_subtitles = ""
        logger.error(f"video_subtitles-Execute error: {traceback.format_exc()}")

    logger.info(f"---get_subtitles_from_video-video_subtitles:{video_subtitles}")
    return video_subtitles


@mcp.tool(description="Summarize the main content of the video.")
def mcp_summarize_video(
    video_url: str = Field(description="The input video in given filepath or url."),
    sample_rate: int = Field(default=2, description="Sample n frames per second."),
    start_time: float = Field(default=0, description="Start time of the video segment in seconds."),
    end_time: float = Field(default=None, description="End time of the video segment in seconds."),
) -> str:
    """summarize the main content of the video."""
    try:
        video_frames = get_video_frames(video_url, sample_rate, start_time, end_time)
        logger.info(f"---len video_frames:{len(video_frames)}")
        interval = 490
        frame_nums = 500
        all_res = []
        for i in range(0, len(video_frames), interval):
            inputs = []
            cur_frames = video_frames[i : i + frame_nums]
            content = create_video_content(
                VIDEO_SUMMARIZE, cur_frames
            )
            inputs.append({"role": "user", "content": content})
            try:
                response = client.chat.completions.create(
                    model=os.getenv("LLM_MODEL_NAME"),  
                    messages=inputs,
                    temperature=0,
                )
                logger.info(f"---response:{response}")
                cur_video_summary = response.choices[0].message.content
            except Exception as e:
                cur_video_summary = ""
            all_res.append(f"summary of video part {int(i / interval + 1)}: {cur_video_summary}")
            logger.info(f"summary of video part {int(i / interval + 1)}: {cur_video_summary}")
        video_summary = "\n".join(all_res)

    except (ValueError, IOError, RuntimeError) as e:
        video_summary = ""
        logger.error(f"video_summary-Execute error: {traceback.format_exc()}")

    logger.info(f"---get_summary_from_video-video_summary:{video_summary}")
    return video_summary


def main():
    from dotenv import load_dotenv
    load_dotenv()
    
    print("Starting Video MCP Server...", file=sys.stderr)
    mcp.run(transport='stdio')


# Make the module callable
def __call__():
    """
    Make the module callable for uvx.
    This function is called when the module is executed directly.
    """
    main()


# Add this for compatibility with uvx
import sys
sys.modules[__name__].__call__ = __call__


# Run the server when the script is executed directly
if __name__ == "__main__":
    main()