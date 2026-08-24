"""Supported media file type groups for document parsing."""

AUDIO_FILE_TYPES = {"mp3", "wav", "m4a", "aac", "flac", "ogg", "opus"}
VIDEO_FILE_TYPES = {"mp4", "mov", "mkv", "webm", "avi", "m4v", "mpeg", "mpg"}
IMAGE_FILE_TYPES = {"png", "jpg", "jpeg", "webp", "gif", "bmp"}
MEDIA_FILE_TYPES = AUDIO_FILE_TYPES | VIDEO_FILE_TYPES | IMAGE_FILE_TYPES
