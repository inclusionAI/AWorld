"""
Comprehensive multi-modal vision processor that handles vision processing separately from template processing.
The pipeline is: Template → Human-readable prompt → Vision processor → LLM-ready inputs.
"""

import base64
import inspect
import math
import urllib.request
from dataclasses import dataclass
from io import BytesIO
from typing import TYPE_CHECKING, BinaryIO, Literal, Optional, TypedDict, Union, List, Dict, Any
from abc import ABC, abstractmethod

import numpy as np
import torch
from PIL import Image
from PIL.Image import Image as ImageObject
from transformers.image_utils import get_image_size, to_numpy_array

if TYPE_CHECKING:
    from av.stream import Stream
    from numpy.typing import NDArray
    from transformers import PreTrainedTokenizer, ProcessorMixin
    from transformers.feature_extraction_sequence_utils import SequenceFeatureExtractor
    from transformers.image_processing_utils import BaseImageProcessor

    class EncodedImage(TypedDict):
        path: Optional[str]
        bytes: Optional[bytes]

    ImageInput = Union[str, bytes, EncodedImage, BinaryIO, "ImageObject"]
    VideoInput = Union[str, BinaryIO, list[list[ImageInput]]]

    class MMProcessor(ProcessorMixin):
        patch_size: int
        image_seq_length: int
        num_additional_image_tokens: int
        vision_feature_select_strategy: Literal["default", "full"]

        def _get_number_of_features(self, orig_height: int, orig_width: int, height: int, width: int) -> int:
            pass


@dataclass
class VisionProcessorConfig:
    """Configuration for vision processing"""
    model_type: str
    image_token: str
    video_token: str
    vision_start: str = ""
    vision_end: str = ""
    processor_class: str = "AutoProcessor"
    expansion_strategy: str = "patch_based"
    image_max_pixels: int = 16384 * 28 * 28
    image_min_pixels: int = 4 * 28 * 28
    video_max_pixels: int = 16384 * 28 * 28
    video_min_pixels: int = 4 * 28 * 28
    video_fps: float = 2.0
    video_maxlen: int = 128


class VisionProcessor(ABC):
    """Abstract base class for vision processing strategies"""

    def __init__(self, config: VisionProcessorConfig):
        self.config = config
        self._validate_config()

    def _validate_config(self):
        """Validate the vision configuration"""
        required_fields = ['image_token', 'video_token']
        for field in required_fields:
            if not hasattr(self.config, field) or getattr(self.config, field) is None:
                raise ValueError(f"Missing required field: {field}")

    @abstractmethod
    def preprocess_images(self, images: List["ImageInput"], processor: Any) -> Dict[str, Any]:
        """Preprocess images for the model"""
        pass

    @abstractmethod
    def preprocess_videos(self, videos: List["VideoInput"], processor: Any) -> Dict[str, Any]:
        """Preprocess videos for the model"""
        pass

    @abstractmethod
    def calculate_image_tokens(self, image_data: Dict[str, Any], processor: Any) -> int:
        """Calculate the number of tokens needed for an image"""
        pass

    @abstractmethod
    def calculate_video_tokens(self, video_data: Dict[str, Any], processor: Any) -> int:
        """Calculate the number of tokens needed for a video"""
        pass

    @abstractmethod
    def expand_vision_tokens(
        self,
        prompt: str,
        images: List["ImageInput"],
        videos: List["VideoInput"],
        processor: Optional[Any],
    ) -> str:
        """Expand vision tokens in the prompt to their actual token representations"""
        pass

    @abstractmethod
    def get_mm_inputs(
        self,
        images: List["ImageInput"],
        videos: List["VideoInput"],
        processor: Optional[Any],
    ) -> Dict[str, torch.Tensor]:
        """Generate multi-modal inputs for the model"""
        pass

    def process_vision_info(self, messages: List[Dict]) -> Dict[str, torch.Tensor]:
        """Process vision information from messages"""
        pass

    # def process_for_llm(
    #     self,
    #     prompt: str,
    #     images: List["ImageInput"],
    #     videos: List["VideoInput"],
    #     processor: Optional[Any],
    #     tokenizer: Any,
    # ) -> Dict[str, torch.Tensor]:
    #     """
    #     Complete pipeline: expand tokens and generate LLM-ready inputs.
    #     Returns inputs that can be used directly with model(**inputs).
    #     """
    #     # Step 1: Expand vision tokens in the prompt
    #     expanded_prompt = self.expand_vision_tokens(prompt, images, videos, processor)

    #     # Step 2: Tokenize the expanded prompt
    #     tokenized_inputs = tokenizer(
    #         expanded_prompt,
    #         return_tensors="pt",
    #         add_special_tokens=True,
    #         padding=True,
    #         truncation=True
    #     )

    #     # Step 3: Generate multi-modal inputs
    #     mm_inputs = self.get_mm_inputs(images, videos, processor)

    #     # Step 4: Combine tokenized inputs with multi-modal inputs
    #     final_inputs = {**tokenized_inputs, **mm_inputs}

    #     return final_inputs

    def process_for_llm(
        self,
        prompt: str,
        elements: List[str],
        mask_flags: List[bool],
        images: List["ImageInput"],
        videos: List["VideoInput"],
        processor: Any,
        tokenizer: Any,
        return_tensors: str = None,
    ) -> Dict[str, torch.Tensor]:
        """
        Process with proper alignment of all tensors (input_ids, attention_mask, labels, action_mask).
        This ensures that when vision tokens are expanded, all corresponding tensors are expanded
        at the same positions, maintaining proper alignment for training and inference.
        """
        import torch

        # Step 1: Tokenize elements to get base tensors with proper alignment
        input_ids = []
        attention_mask = []
        labels = []
        action_mask = []

        # Add BOS token if needed
        if tokenizer.bos_token and tokenizer.add_bos_token:
            input_ids.append(tokenizer.bos_token_id)
            attention_mask.append(1)
            labels.append(-100)
            action_mask.append(0)

        images_to_process = [image for image in images]
        videos_to_process = [video for video in videos]
        # Step 2: Process each element with vision token expansion
        for element, mask_flag in zip(elements, mask_flags):
            # Check if element contains vision tokens
            if self._contains_vision_tokens(element):
                # Expand vision tokens in this element
                # Number of images and videos should be equal to the total number of vision tokens in the element
                # We check whether all images and videos are processed later.
                expanded_element = self.expand_vision_tokens(element, images_to_process, videos_to_process, processor)
                cur_input_ids = tokenizer.encode(expanded_element, add_special_tokens=False)
            else:
                cur_input_ids = tokenizer.encode(element, add_special_tokens=False)

            # Add tokens with proper alignment
            input_ids.extend(cur_input_ids)
            attention_mask.extend([1] * len(cur_input_ids))

            if mask_flag:
                labels.extend([-100] * len(cur_input_ids))
                action_mask.extend([0] * len(cur_input_ids))
            else:
                labels.extend(cur_input_ids)
                action_mask.extend([1] * len(cur_input_ids))

        assert len(images_to_process) == len(
            videos_to_process) == 0, f"All images and videos should be processed, but got {len(images_to_process)} images and {len(videos_to_process)} videos left for vision template {self.config.model_type}."

        # Step 3: Create base inputs
        inputs = {
            'input_ids': input_ids,
            'attention_mask': attention_mask,
            'labels': labels,
            'action_mask': action_mask
        }

        # Convert to tensors if requested
        if return_tensors == "pt":
            inputs = {k: torch.tensor([v]) for k, v in inputs.items()}

        # Step 4: Add vision inputs
        mm_inputs = self.get_mm_inputs(images, videos, processor)
        inputs.update(mm_inputs)

        return inputs

    def _contains_vision_tokens(self, text: str) -> bool:
        """Check if text contains vision tokens"""
        return self.config.image_token in text or self.config.video_token in text


class PatchBasedProcessor(VisionProcessor):
    """Patch-based vision processor (used by Qwen-VL, LLaVA, etc.)

    Supports multiple image input formats:
    - File paths (str): "/path/to/image.jpg"
    - URLs (str): "https://example.com/image.jpg"
    - Base64 strings (str): "data:image/jpeg;base64,/9j/4AAQ..." or raw base64
    - PIL Image objects
    - Bytes objects
    - File-like objects
    - Dict format: {"path": "/path/to/image.jpg"} or {"bytes": b"image_data"}
    """

    def _load_image_from_input(self, image_input) -> "ImageObject":
        """Load image from various input formats including URL and base64"""
        from PIL import Image

        # Handle PIL Image objects directly
        if hasattr(image_input, 'width') and hasattr(image_input, 'height'):
            return image_input

        # Handle string inputs (file path, URL, or base64)
        if isinstance(image_input, str):
            # Check if it's a URL
            if image_input.startswith(('http://', 'https://')):
                try:
                    with urllib.request.urlopen(image_input) as response:
                        image_data = response.read()
                    return Image.open(BytesIO(image_data))
                except Exception as e:
                    raise ValueError(f"Failed to load image from URL {image_input}: {e}")

            # Check if it's a base64 string
            elif image_input.startswith('data:image/') or image_input.startswith('data:application/octet-stream'):
                # Handle data URL format: data:image/jpeg;base64,/9j/4AAQ...
                try:
                    # Extract the base64 part after the comma
                    base64_data = image_input.split(',', 1)[1]
                    image_data = base64.b64decode(base64_data)
                    return Image.open(BytesIO(image_data))
                except Exception as e:
                    raise ValueError(f"Failed to decode base64 image: {e}")

            elif image_input.startswith('iVBORw0KGgo') or len(image_input) > 100:
                # Likely a raw base64 string (common for PNG images starting with iVBORw0KGgo)
                try:
                    image_data = base64.b64decode(image_input)
                    return Image.open(BytesIO(image_data))
                except Exception as e:
                    raise ValueError(f"Failed to decode base64 image: {e}")

            # Assume it's a file path
            else:
                print(f"Loading image from file path: {image_input}")
                return Image.open(image_input)

        # Handle bytes
        elif isinstance(image_input, bytes):
            return Image.open(BytesIO(image_input))

        # Handle file-like objects
        elif hasattr(image_input, 'read'):
            return Image.open(image_input)

        # Handle dict format
        elif isinstance(image_input, dict):
            if image_input.get("bytes") is not None:
                return Image.open(BytesIO(image_input["bytes"]))
            elif image_input.get("path") is not None:
                return Image.open(image_input["path"])
            else:
                raise ValueError("Invalid image dict format")

        else:
            raise ValueError(f"Unsupported image input type: {type(image_input)}")

    def _preprocess_single_image(self, image: "ImageObject", **kwargs) -> "ImageObject":
        """Preprocess a single image"""
        if (image.width * image.height) > self.config.image_max_pixels:
            resize_factor = math.sqrt(self.config.image_max_pixels / (image.width * image.height))
            width, height = int(image.width * resize_factor), int(image.height * resize_factor)
            image = image.resize((width, height))

        if (image.width * image.height) < self.config.image_min_pixels:
            resize_factor = math.sqrt(self.config.image_min_pixels / (image.width * image.height))
            width, height = int(image.width * resize_factor), int(image.height * resize_factor)
            image = image.resize((width, height))

        if image.mode != "RGB":
            image = image.convert("RGB")

        return image

    def _regularize_images(self, images: List["ImageInput"]) -> List["ImageObject"]:
        """Regularize images to avoid errors"""
        results = []
        for image in images:
            # Use the new helper method to handle all input formats
            pil_image = self._load_image_from_input(image)
            results.append(self._preprocess_single_image(pil_image))

        return results

    def _regularize_videos(self, videos: List["VideoInput"]) -> List[List["ImageObject"]]:
        """Regularize videos to avoid errors"""
        results = []
        for video in videos:
            frames: List["ImageObject"] = []

            # Check if video is nested images
            if isinstance(video, list) and all(isinstance(frame, (str, BinaryIO, dict)) for frame in video):
                # Use the new image loading method for each frame
                for frame in video:
                    try:
                        pil_image = self._load_image_from_input(frame)
                        frames.append(pil_image)
                    except Exception as e:
                        raise ValueError(f"Invalid image found in video frames: {e}")
            else:
                # Process actual video file
                import av
                container = av.open(video, "r")
                video_stream = next(stream for stream in container.streams if stream.type == "video")

                # Calculate sample indices
                total_frames = video_stream.frames
                if total_frames == 0:  # infinite video
                    sample_indices = np.linspace(0, self.config.video_maxlen - 1, self.config.video_maxlen).astype(np.int32)
                else:
                    sample_frames = max(1, math.floor(float(video_stream.duration * video_stream.time_base) * self.config.video_fps))
                    sample_frames = min(total_frames, self.config.video_maxlen, sample_frames)
                    sample_indices = np.linspace(0, total_frames - 1, sample_frames).astype(np.int32)

                container.seek(0)
                for frame_idx, frame in enumerate(container.decode(video_stream)):
                    if frame_idx in sample_indices:
                        frames.append(frame.to_image())

            frames = self._regularize_images(frames)
            results.append(frames)

        return results

    def preprocess_images(self, images: List["ImageInput"], processor: Any) -> Dict[str, Any]:
        """Preprocess images for the model"""
        if not images:
            return {}

        image_processor = getattr(processor, "image_processor", None)
        if image_processor is None:
            raise ValueError("Image processor not found")

        images = self._regularize_images(images)
        return image_processor(images, return_tensors="pt")

    def preprocess_videos(self, videos: List["VideoInput"], processor: Any) -> Dict[str, Any]:
        """Preprocess videos for the model"""
        if not videos:
            return {}

        video_processor = getattr(processor, "video_processor", getattr(processor, "image_processor", None))
        if video_processor is None:
            raise ValueError("Video processor not found")

        videos = self._regularize_videos(videos)

        # Handle different video processor interfaces
        if "videos" in inspect.signature(video_processor.preprocess).parameters:
            return video_processor(images=None, videos=videos, return_tensors="pt")
        else:
            return video_processor(videos, return_tensors="pt")

    def calculate_image_tokens(self, image_data: Dict[str, Any], processor: Any) -> int:
        """Calculate the number of tokens needed for an image

        Uses two approaches:
        1. Grid-based (HuggingFace method): Uses image_grid_thw and merge_size
           - More accurate for models like Qwen-VL
           - Accounts for hierarchical token merging
        2. Patch-based (fallback): Uses image dimensions and patch_size
           - Standard approach for most ViT-based models
           - Assumes each patch corresponds to one token
        """
        if "pixel_values" in image_data:
            # Try grid-based calculation first (HuggingFace method)
            if "image_grid_thw" in image_data:
                grid_info = image_data["image_grid_thw"]
                if isinstance(grid_info, torch.Tensor):
                    grid_prod = grid_info.prod().item()
                elif isinstance(grid_info, list):
                    grid_prod = math.prod(grid_info)
                else:
                    grid_prod = grid_info

                # Get merge_size from processor
                merge_size = getattr(processor, "merge_size", 1)
                merge_length = merge_size ** 2

                num_image_tokens = grid_prod // merge_length
                return max(1, num_image_tokens)

            # Fallback to patch-based calculation
            height, width = get_image_size(to_numpy_array(image_data["pixel_values"][0]))
            image_seqlen = (height // processor.patch_size) * (width // processor.patch_size)
            if hasattr(processor, 'num_additional_image_tokens'):
                image_seqlen += processor.num_additional_image_tokens
            if hasattr(processor, 'vision_feature_select_strategy') and processor.vision_feature_select_strategy == "default":
                image_seqlen -= 1
            return image_seqlen
        return 1

    def calculate_video_tokens(self, video_data: Dict[str, Any], processor: Any) -> int:
        """Calculate the number of tokens needed for a video"""
        if "pixel_values" in video_data:
            # For videos, we need to calculate based on frames
            video_tensor = video_data["pixel_values"][0]
            if len(video_tensor.shape) > 3:  # Has frame dimension
                num_frames = video_tensor.shape[0]
                height, width = get_image_size(to_numpy_array(video_tensor[0]))
                frame_seqlen = (height // processor.patch_size) * (width // processor.patch_size)
                if hasattr(processor, 'num_additional_image_tokens'):
                    frame_seqlen += processor.num_additional_image_tokens
                if hasattr(processor, 'vision_feature_select_strategy') and processor.vision_feature_select_strategy == "default":
                    frame_seqlen -= 1
                return frame_seqlen * num_frames
            else:
                # Single frame video
                return self.calculate_image_tokens(video_data, processor)
        return 1

    def expand_vision_tokens(
        self,
        prompt: str,
        images: List["ImageInput"],
        videos: List["VideoInput"],
        processor: Optional[Any],
    ) -> str:
        """Expand vision tokens in the prompt to their actual token representations"""
        if processor is None:
            raise ValueError("Processor is required for vision processing")

        # Validate that number of placeholders matches number of inputs
        num_image_placeholders = prompt.count(self.config.image_token)
        num_video_placeholders = prompt.count(self.config.video_token)

        # if len(images) != num_image_placeholders:
        #     raise ValueError(f"Number of images ({len(images)}) doesn't match placeholders ({num_image_placeholders})")
        # if len(videos) != num_video_placeholders:
        #     raise ValueError(f"Number of videos ({len(videos)}) doesn't match placeholders ({num_video_placeholders})")
        images_slice = [images.pop(0) for _ in range(num_image_placeholders)]
        videos_slice = [videos.pop(0) for _ in range(num_video_placeholders)]
        # Preprocess images and videos to get individual token counts

        processed_images = [self.preprocess_images([image], processor) for image in images_slice]
        processed_videos = [self.preprocess_videos([video], processor) for video in videos_slice]

        expanded_prompt = prompt
        if self.config.image_token in expanded_prompt and processed_images:
            parts = expanded_prompt.split(self.config.image_token)
            expanded_parts = [parts[0]]
            for idx in range(len(parts) - 1):
                if idx < len(processed_images):
                    processed_image = processed_images[idx]
                    if "pixel_values" in processed_image:
                        image_tokens = self.calculate_image_tokens(processed_image, processor)
                        replacement = self.config.image_token * image_tokens
                    else:
                        replacement = self.config.image_token
                else:
                    replacement = self.config.image_token
                expanded_parts.append(replacement)
                expanded_parts.append(parts[idx + 1])
            expanded_prompt = ''.join(expanded_parts)

        # Expand video tokens sequentially - each token gets replaced with its corresponding video
        if self.config.video_token in expanded_prompt and processed_videos:
            parts = expanded_prompt.split(self.config.video_token)
            expanded_parts = [parts[0]]
            for idx in range(len(parts) - 1):
                if idx < len(processed_videos):
                    processed_video = processed_videos[idx]
                    if "pixel_values" in processed_video:
                        video_tokens = self.calculate_video_tokens(processed_video, processor)
                        replacement = self.config.video_token * video_tokens
                    else:
                        replacement = self.config.video_token
                else:
                    replacement = self.config.video_token
                expanded_parts.append(replacement)
                expanded_parts.append(parts[idx + 1])
            expanded_prompt = ''.join(expanded_parts)

        return expanded_prompt

    def get_mm_inputs(
        self,
        images: List["ImageInput"],
        videos: List["VideoInput"],
        processor: Optional[Any],
    ) -> Dict[str, torch.Tensor]:
        """Generate multi-modal inputs for the model"""
        mm_inputs = {}

        # Process images
        if images:
            mm_inputs.update(self.preprocess_images(images, processor))

        # Process videos
        if videos:
            mm_inputs.update(self.preprocess_videos(videos, processor))

        return mm_inputs

    def process_vision_info(self, messages: List[Dict], processor: Any):
        """Process vision information from messages"""
        image_message_types = ["image", "image_url", "image_base64"]
        images = []
        for message in messages:
            for content in message["content"]:
                if content["type"] in image_message_types:
                    content_type = content["type"]
                    images.append(content[content_type])
        mm_inputs = self.get_mm_inputs(images, [], processor)
        return mm_inputs


class QwenVLProcessor(PatchBasedProcessor):
    """Qwen-VL specific processor with custom image preprocessing"""

    def _preprocess_single_image(self, image: "ImageObject", **kwargs) -> "ImageObject":
        """Qwen-VL specific image preprocessing"""
        image = super()._preprocess_single_image(image, **kwargs)

        # Qwen-VL specific adjustments
        if min(image.width, image.height) < 28:
            width, height = max(image.width, 28), max(image.height, 28)
            image = image.resize((width, height))

        if image.width / image.height > 200:
            width, height = image.height * 180, image.height
            image = image.resize((width, height))

        if image.height / image.width > 200:
            width, height = image.width, image.width * 180
            image = image.resize((width, height))

        return image

    def calculate_image_tokens(self, image_data: Dict[str, Any], processor: Any) -> int:
        """Qwen-VL specific token calculation using grid-based approach"""
        if "image_grid_thw" in image_data:
            # Use grid information for more accurate token calculation
            grid_info = image_data["image_grid_thw"]
            if isinstance(grid_info, torch.Tensor):
                grid_prod = grid_info.prod().item()
            elif isinstance(grid_info, list):
                grid_prod = math.prod(grid_info)
            else:
                grid_prod = grid_info

            # Get merge_size from processor (Qwen-VL typically uses merge_size=2)
            merge_size = getattr(processor, "merge_size", 2)
            merge_length = merge_size ** 2

            num_image_tokens = grid_prod // merge_length
            return max(1, num_image_tokens)

        # Fallback to standard calculation
        return super().calculate_image_tokens(image_data, processor)

    def expand_vision_tokens(
        self,
        prompt: str,
        images: List["ImageInput"],
        videos: List["VideoInput"],
        processor: Optional[Any],
    ) -> str:
        """Qwen-VL specific token expansion with vision tags"""
        expanded_prompt = super().expand_vision_tokens(prompt, images, videos, processor)

        return expanded_prompt


class LlavaProcessor(PatchBasedProcessor):
    """LLaVA specific processor"""

    def calculate_image_tokens(self, image_data: Dict[str, Any], processor: Any) -> int:
        """LLaVA specific token calculation"""
        if "pixel_values" in image_data:
            height, width = get_image_size(to_numpy_array(image_data["pixel_values"][0]))
            image_seqlen = (height // processor.patch_size) * (width // processor.patch_size)
            if hasattr(processor, 'num_additional_image_tokens'):
                image_seqlen += processor.num_additional_image_tokens
            if hasattr(processor, 'vision_feature_select_strategy') and processor.vision_feature_select_strategy == "default":
                image_seqlen -= 1
            return image_seqlen
        return 1


VISION_PROCESSORS: Dict[str, VisionProcessor] = {}

model_type_to_processor_class = {
    "qwen_vl": QwenVLProcessor,
    "llava": LlavaProcessor,
    "gemma3": PatchBasedProcessor,
    "paligemma": PatchBasedProcessor,
    "internvl": PatchBasedProcessor,
    "minicpm": PatchBasedProcessor,
    "mllama": PatchBasedProcessor,
    "pixtral": PatchBasedProcessor,
    "video_llava": PatchBasedProcessor,
    "patch_based": PatchBasedProcessor,
}


def register_processor(template_name: str, config: VisionProcessorConfig):
    """Register a vision processor for a template"""
    processor_class = model_type_to_processor_class.get(config.model_type)
    if processor_class is None:
        raise ValueError(f"No processor class found for model type: {config.model_type}")
    VISION_PROCESSORS[template_name] = processor_class(config)


def register(cls, template_name: str, config: VisionProcessorConfig, processor_class: type = None):
    """Register a vision processor for a template"""
    if processor_class is not None:
        # If processor_class is provided, use it directly
        VISION_PROCESSORS[template_name] = processor_class(config)
    else:
        # Use the global register_processor function
        register_processor(template_name, config)


def get_processor(template_name: str) -> Optional[VisionProcessor]:
    """Get vision processor for a template"""
    return VISION_PROCESSORS.get(template_name)


def get_processor_config(template_name: str) -> Optional[VisionProcessorConfig]:
    """Get vision config for a template"""
    processor = get_processor(template_name)
    return processor.config if processor else None


def is_vision_template(template_name: str) -> bool:
    """Check if template supports vision"""
    return template_name in VISION_PROCESSORS


def list_vision_templates() -> List[str]:
    """List all vision-enabled templates"""
    return list(VISION_PROCESSORS.keys())
