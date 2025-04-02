# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""Audio transcription and analysis prompts.

Returns:
    str: Prompt templates for audio processing tasks
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
