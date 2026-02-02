# coding: utf-8
# Copyright (c) inclusionAI.
from abc import ABC, abstractmethod

from typing import Any, Optional, List
from aworld.ralph_loop.mission.types import Mission, MissionType


class MissionProcessor(ABC):
    """Base class for mission processors."""

    @abstractmethod
    def to_mission(self, user_input: Any, **kwargs) -> Mission:
        """
        Process user input into a Mission object.

        Args:
            user_input: The user input to process
            **kwargs: Additional processing parameters

        Returns:
            Mission: Processed mission object
        """

    def _create_mission(
            self,
            original: Any,
            text: str,
            input_type: MissionType,
            desc: str = "",
            **kwargs
    ) -> Mission:
        return Mission(
            original=original,
            text=text,
            input_type=input_type,
            desc=desc,
            metadata=kwargs
        )


class TextProcessor(MissionProcessor):
    """Processor for text input."""

    def to_mission(self, user_input: Any, **kwargs) -> Mission:
        """Process text input."""
        if isinstance(user_input, str):
            text = user_input
        elif isinstance(user_input, dict):
            text = user_input.get("text", str(user_input))
        else:
            # may throw error
            text = str(user_input)

        desc = kwargs.get("desc", "")

        return self._create_mission(
            original=user_input,
            text=text,
            input_type='text',
            desc=desc,
            **kwargs
        )


class ImageProcessor(MissionProcessor):
    """Processor for image input."""

    def to_mission(self, user_input: Any, **kwargs) -> Mission:
        """Process image input."""


class AudioProcessor(MissionProcessor):
    """Processor for audio input."""

    def to_mission(self, user_input: Any, **kwargs) -> Mission:
        """Process audio input."""


class VideoProcessor(MissionProcessor):
    """Processor for video input."""

    def to_mission(self, user_input: Any, **kwargs) -> Mission:
        """Process video input."""


class FileProcessor(MissionProcessor):
    """Processor for file input."""

    def to_mission(self, user_input: Any, **kwargs) -> Mission:
        """Process file input."""


class HybridProcessor(MissionProcessor):
    """Processor for hybrid multimodal input."""

    def __init__(self):
        self.text_processor = TextProcessor()
        self.image_processor = ImageProcessor()
        self.audio_processor = AudioProcessor()
        self.video_processor = VideoProcessor()
        self.file_processor = FileProcessor()

    def to_mission(self, user_input: Any, **kwargs) -> Mission:
        """Process hybrid input."""

        return self.text_processor.to_mission(user_input, **kwargs)


def create_processor(input_type: Optional[MissionType] = None) -> MissionProcessor:
    """Create appropriate processor for the input.

    Args:
        input_type: Explicitly specified input type.

    Returns:
        MissionProcessor: Appropriate processor
    """
    if input_type:
        processors = {
            "text": TextProcessor(),
            "json": TextProcessor(),
            "image": ImageProcessor(),
            "voice": AudioProcessor(),
            "video": VideoProcessor(),
            "file": FileProcessor(),
            "hybrid": HybridProcessor(),
        }
        return processors.get(input_type, TextProcessor())
    return HybridProcessor()


def to_mission(user_input: Any,
               input_type: Optional[MissionType] = None,
               processor: Optional[MissionProcessor] = None,
               **kwargs) -> Mission:
    """Utility function to process user input in one call.

    Args:
        user_input: User input
        input_type: Optional input type hint
        processor: Custom processor to process user input
        **kwargs: Additional parameters

    Returns:
        Unified Mission structure
    """
    if not processor:
        processor = create_processor(input_type)
    return processor.to_mission(user_input, **kwargs)
