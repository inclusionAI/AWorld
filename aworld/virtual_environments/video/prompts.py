# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""Video transcription and analysis prompts.

Returns:
    str: Prompt templates for video processing tasks
"""

VIDEO_EXTRACT_SUBTITLES = (
    "Input is a video file consisting a series of base64 encoded frames which are in jpeg format. "
    "Extract all subtitles (if present) in the video. "
    "Return a json string with the following format: "
    '{{"video_subtitles": "extracted subtitles from video"}}'
)

VIDEO_ANALYZE = (
    "Input is a video file consisting a series of base64 encoded frames which are in jpeg format. "
    "Given user's question: {question}, "
    "analyze the video content and answer the question following these guidelines:\n"
    "1. Watch the entire video carefully\n"
    "2. Consider visual elements, speech, and sounds\n"
    "3. Identify key actions and events\n"
    "4. Note any text displayed in the video\n"
    "Return a json string with the following format: "
    '{{"video_analysis_result": "analysis result given question and video content"}}'
)

VIDEO_SUMMARIZE = (
    "Input is a video file consisting a series of base64 encoded frames which are in jpeg format. "
    "Summarize the main content of the video. "
    "Include key points, main topics, and important visual elements. "
    "Return a json string with the following format: "
    '{{"video_summary": "concise summary of the video content"}}'
)
