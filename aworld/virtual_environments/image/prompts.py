IMAGE_OCR = 'Input is a base64 encoded image. Read text from image if present. Return a json string with the following format: {"image_text": "text from image"}'

GUILDE_LINE = """
1. Careful visual inspection
2. Contextual reasoning
3. Text transcription where relevant
4. Logical deduction from visual evidence
"""

IMAGE_REASONING = """
Input is a base64 encoded image. Given user's task: {task}, solve it following the guide line: 
1. Careful visual inspection
2. Contextual reasoning
3. Text transcription where relevant
4. Logical deduction from visual evidence
Return a json string with the following format: {{\"image_reasoning_result\": \"reasoning result given task and image\"}}"""
