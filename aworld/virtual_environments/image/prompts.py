"""Image OCR prompt for extracting text from images.

Returns:
    str: A JSON string format prompt containing the extracted image text
"""

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
