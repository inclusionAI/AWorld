# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""Tool agent prompts.

Returns:
    str: Prompt templates for processing tasks
"""

AUDIO_TRANSCRIBE = (
    "Input is a base64 encoded audio file. Transcribe all speech in the audio. "
    "Return a json string with the following format: "
    '{{"audio_text": "transcribed text from audio"}}'
)

AUDIO_ANALYZE = (
    "Input is a base64 encoded audio file. Given user's question: {question}, "
    "analyze the audio content and answer the question following these guidelines:\n"
    "1. Listen carefully to all speech and sounds\n"
    "2. Consider tone, emotion, and context\n"
    "3. Transcribe relevant speech\n"
    "4. Identify background sounds if relevant\n"
    "Return a json string with the following format: "
    '{{"audio_analysis_result": "analysis result given question and audio content"}}'
)

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
