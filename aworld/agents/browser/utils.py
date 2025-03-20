# coding: utf-8
import requests
import json
from PIL import Image
from io import BytesIO
import os
from typing import Any, Optional, Type
import base64

from langchain_core.messages import (
	AIMessage,
	BaseMessage,
	HumanMessage,
	SystemMessage,
	ToolMessage,
)

from aworld.logs.util import logger


def extract_json_from_model_output(content: str) -> dict:
	"""Extract JSON from model output, handling both plain JSON and code-block-wrapped JSON."""
	try:
		# If content is wrapped in code blocks, extract just the JSON part
		if '```' in content:
			# Find the JSON content between code blocks
			content = content.split('```')[1]
			# Remove language identifier if present (e.g., 'json\n')
			if '\n' in content:
				content = content.split('\n', 1)[1]
		# Parse the cleaned content
		return json.loads(content)
	except json.JSONDecodeError as e:
		logger.warning(f'Failed to parse model output: {content} {str(e)}')
		raise ValueError('Could not parse response.')


def convert_input_messages(input_messages: list[BaseMessage], model_name: Optional[str]) -> list[BaseMessage]:
	"""Convert input messages to a format that is compatible with the planner model"""
	if model_name is None:
		return input_messages
	if model_name == 'deepseek-reasoner' or model_name.startswith('deepseek-r1'):
		converted_input_messages = _convert_messages_for_non_function_calling_models(input_messages)
		merged_input_messages = _merge_successive_messages(converted_input_messages, HumanMessage)
		merged_input_messages = _merge_successive_messages(merged_input_messages, AIMessage)
		return merged_input_messages
	return input_messages


def _convert_messages_for_non_function_calling_models(input_messages: list[BaseMessage]) -> list[BaseMessage]:
	"""Convert messages for non-function-calling models"""
	output_messages = []
	for message in input_messages:
		if isinstance(message, HumanMessage):
			output_messages.append(message)
		elif isinstance(message, SystemMessage):
			output_messages.append(message)
		elif isinstance(message, ToolMessage):
			output_messages.append(HumanMessage(content=message.content))
		elif isinstance(message, AIMessage):
			# check if tool_calls is a valid JSON object
			if message.tool_calls:
				tool_calls = json.dumps(message.tool_calls)
				output_messages.append(AIMessage(content=tool_calls))
			else:
				output_messages.append(message)
		else:
			raise ValueError(f'Unknown message type: {type(message)}')
	return output_messages


def _merge_successive_messages(messages: list[BaseMessage], class_to_merge: Type[BaseMessage]) -> list[BaseMessage]:
	"""Some models like deepseek-reasoner dont allow multiple human messages in a row. This function merges them into one."""
	merged_messages = []
	streak = 0
	for message in messages:
		if isinstance(message, class_to_merge):
			streak += 1
			if streak > 1:
				if isinstance(message.content, list):
					merged_messages[-1].content += message.content[0]['text']  # type:ignore
				else:
					merged_messages[-1].content += message.content
			else:
				merged_messages.append(message)
		else:
			merged_messages.append(message)
			streak = 0
	return merged_messages


def save_conversation(input_messages: list[BaseMessage], response: Any, target: str, encoding: Optional[str] = None) -> None:
	"""Save conversation history to file."""

	# create folders if not exists
	os.makedirs(os.path.dirname(target), exist_ok=True)

	with open(
		target,
		'w',
		encoding=encoding,
	) as f:
		_write_messages_to_file(f, input_messages)
		_write_response_to_file(f, response)


def _write_messages_to_file(f: Any, messages: list[BaseMessage]) -> None:
	"""Write messages to conversation file"""
	for message in messages:
		f.write(f' {message.__class__.__name__} \n')

		if isinstance(message.content, list):
			for item in message.content:
				if isinstance(item, dict) and item.get('type') == 'text':
					f.write(item['text'].strip() + '\n')
		elif isinstance(message.content, str):
			try:
				content = json.loads(message.content)
				f.write(json.dumps(content, indent=2) + '\n')
			except json.JSONDecodeError:
				f.write(message.content.strip() + '\n')

		f.write('\n')


def _write_response_to_file(f: Any, response: Any) -> None:
	"""Write model response to conversation file"""
	f.write(' RESPONSE\n')
	f.write(json.dumps(json.loads(response.model_dump_json(exclude_unset=True)), indent=2))

def encode_image_from_url(image_url):
    """从URL获取图片并编码为base64"""
    response = requests.get(image_url)
    image = Image.open(BytesIO(response.content))

    # 调整图片大小以避免过大的请求
    max_size = 1024
    if max(image.size) > max_size:
        ratio = max_size / max(image.size)
        new_size = (int(image.size[0] * ratio), int(image.size[1] * ratio))
        image = image.resize(new_size, Image.LANCZOS)

    buffered = BytesIO()
    image_format = image.format if image.format else 'JPEG'
    image.save(buffered, format=image_format)
    img_str = base64.b64encode(buffered.getvalue()).decode()
    return img_str


def encode_image_from_file(image_path):
    """从本地文件读取图片并编码为base64"""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode()