import logging
from typing import List, TypeVar, Type
from pydantic import BaseModel, Field, ValidationError
import json
import json_repair

# Define generic type
T = TypeVar('T', bound=BaseModel)

def parse_json_to_model(json_data: str, model_class: Type[T]) -> T:
    """
    JSON parsing utility method - supports processing JSON with code block markers returned by large models

    Args:
        json_data (str): JSON string (supports format with ```json``` code block markers)
        model_class (Type[T]): Pydantic model class type

    Returns:
        T: Parsed model instance

    Raises:
        json.JSONDecodeError: When JSON format is invalid
        ValidationError: When data doesn't match model structure

    Example:
        >>> # Regular JSON string
        >>> json_str = '{"query": ["test query"], "rationale": "test rationale"}'
        >>> result = parse_json_to_model(json_str, SearchQueryList)

        >>> # JSON with code block markers (common format returned by large models)
        >>> json_with_blocks = '''```json
        ... {"query": ["test"], "rationale": "test"}
        ... ```'''
        >>> result = parse_json_to_model(json_with_blocks, SearchQueryList)
    """
    try:
        # Clean possible code block markers
        cleaned_json = json_data.strip()

        # Remove opening code block markers
        if cleaned_json.startswith('```json'):
            cleaned_json = cleaned_json[7:]  # Remove '```json'
        elif cleaned_json.startswith('```'):
            cleaned_json = cleaned_json[3:]   # Remove '```'

        # Remove closing ``` markers
        if cleaned_json.endswith('```'):
            cleaned_json = cleaned_json[:-3]
        # print("cleaned_json:", cleaned_json)

        # Clean whitespace again
        cleaned_json = cleaned_json.strip()

        # Parse JSON string to dictionary or list
        parsed_data = json.loads(cleaned_json)

        # If parsed result is a list, take the first element
        logging.debug(f"parsed_data: {parsed_data}")
        if isinstance(parsed_data, list):
            if len(parsed_data) == 0:
                raise ValueError(f"Empty list cannot be converted to {model_class.__name__}")
            data_dict = parsed_data[0]
        elif isinstance(parsed_data, dict):
            data_dict = parsed_data
        else:
            raise ValueError(f"Parsed data must be a dict or list, got {type(parsed_data)}")
        logging.debug(f"data_dict:, {data_dict}")
        # Use Pydantic model validation and create instance
        return model_class(**data_dict)

    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(f"Invalid JSON format: {e.msg}", e.doc, e.pos)
    except ValidationError as e:
        raise ValidationError(f"Data validation failed for {model_class.__name__}: {e}")
    except Exception as e:
        raise Exception(f"Unexpected error while parsing JSON to {model_class.__name__}: {str(e)}")

def parse_json_to_model_list(json_data: str, model_class: Type[T]) -> List[T]:
    """
    JSON parsing utility method - specifically handles JSON lists, returns model instance list

    Args:
        json_data (str): JSON string (should be an array)
        model_class (Type[T]): Pydantic model class type

    Returns:
        List[T]: Parsed model instance list

    Raises:
        json.JSONDecodeError: When JSON format is invalid
        ValidationError: When data doesn't match model structure
        ValueError: When JSON is not in list format

    Example:
        >>> json_str = '[{"title": "test", "doc": "content"}]'
        >>> results = parse_json_to_model_list(json_str, AworldSearch)
    """
    try:
        # Clean possible code block markers
        cleaned_json = json_data.strip()

        # Remove opening code block markers
        if cleaned_json.startswith('```json'):
            cleaned_json = cleaned_json[7:]  # Remove '```json'
        elif cleaned_json.startswith('```'):
            cleaned_json = cleaned_json[3:]   # Remove '```'

        # Remove closing ``` markers
        if cleaned_json.endswith('```'):
            cleaned_json = cleaned_json[:-3]

        # Clean whitespace again
        cleaned_json = cleaned_json.strip()

        # Parse JSON string
        parsed_data = json.loads(cleaned_json)

        # Ensure parsed result is a list
        if not isinstance(parsed_data, list):
            raise ValueError(f"Expected JSON array, got {type(parsed_data)}")

        # Convert each dictionary in the list to model instances
        model_instances = []
        for i, item in enumerate(parsed_data):
            if not isinstance(item, dict):
                raise ValueError(f"List item {i} is not a dictionary: {type(item)}")
            model_instances.append(model_class(**item))

        return model_instances

    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(f"Invalid JSON format: {e.msg}", e.doc, e.pos)
    except ValidationError as e:
        raise ValidationError(f"Data validation failed for {model_class.__name__}: {e}")
    except Exception as e:
        raise Exception(f"Unexpected error while parsing JSON list to {model_class.__name__}: {str(e)}")

def extract_json_from_text(text: str, model_class: Type[T]) -> T:
    """
    Extract JSON content from text, removing non-JSON content before and after
    
    Args:
        text (str): Original text containing JSON
        
    Returns:
        str: Extracted clean JSON string
        
    Example:
        >>> text = '''Based on comprehensive analysis of execution details, I can now verify this solution:
        ... ```json
        ... {"rating": "3.8", "verify_list": []}
        ... ```
        ... ——--------------'''
        >>> json_str = extract_json_from_text(text)
        >>> print(json_str)  # '{"rating": "3.8", "verify_list": []}'
    """
    try:
        data_dict = json_repair.repair_json(text, return_objects=True)
        return model_class(**data_dict)
    except json.JSONDecodeError as e:
        raise ValueError(f"Extracted content is not valid JSON: {e.msg}")
    except Exception as e:
        raise ValueError(f"Error extracting JSON from text: {str(e)}")
