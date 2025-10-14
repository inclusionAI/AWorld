import logging
from typing import List, TypeVar, Type
from pydantic import BaseModel, Field, ValidationError
import json
import json_repair

# 定义泛型类型
T = TypeVar('T', bound=BaseModel)

def parse_json_to_model(json_data: str, model_class: Type[T]) -> T:
    """
    JSON解析工具方法 - 支持处理大模型返回的带代码块标记的JSON

    Args:
        json_data (str): JSON字符串（支持带有```json```代码块标记的格式）
        model_class (Type[T]): Pydantic模型类类型

    Returns:
        T: 解析后的模型实例

    Raises:
        json.JSONDecodeError: 当JSON格式无效时
        ValidationError: 当数据不符合模型结构时

    Example:
        >>> # 普通JSON字符串
        >>> json_str = '{"query": ["test query"], "rationale": "test rationale"}'
        >>> result = parse_json_to_model(json_str, SearchQueryList)

        >>> # 带代码块标记的JSON（大模型常见返回格式）
        >>> json_with_blocks = '''```json
        ... {"query": ["test"], "rationale": "test"}
        ... ```'''
        >>> result = parse_json_to_model(json_with_blocks, SearchQueryList)
    """
    try:
        # 清理可能的代码块标记
        cleaned_json = json_data.strip()

        # 移除开头的代码块标记
        if cleaned_json.startswith('```json'):
            cleaned_json = cleaned_json[7:]  # 移除 '```json'
        elif cleaned_json.startswith('```'):
            cleaned_json = cleaned_json[3:]   # 移除 '```'

        # 移除结尾的```标记
        if cleaned_json.endswith('```'):
            cleaned_json = cleaned_json[:-3]
        # print("cleaned_json:", cleaned_json)

        # 再次清理空白字符
        cleaned_json = cleaned_json.strip()

        # 解析JSON字符串为字典或列表
        parsed_data = json.loads(cleaned_json)

        # 如果解析结果是列表，取第一个元素
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
        # 使用Pydantic模型验证并创建实例
        return model_class(**data_dict)

    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(f"Invalid JSON format: {e.msg}", e.doc, e.pos)
    except ValidationError as e:
        raise ValidationError(f"Data validation failed for {model_class.__name__}: {e}")
    except Exception as e:
        raise Exception(f"Unexpected error while parsing JSON to {model_class.__name__}: {str(e)}")

def parse_json_to_model_list(json_data: str, model_class: Type[T]) -> List[T]:
    """
    JSON解析工具方法 - 专门处理JSON列表，返回模型实例列表

    Args:
        json_data (str): JSON字符串（应该是一个数组）
        model_class (Type[T]): Pydantic模型类类型

    Returns:
        List[T]: 解析后的模型实例列表

    Raises:
        json.JSONDecodeError: 当JSON格式无效时
        ValidationError: 当数据不符合模型结构时
        ValueError: 当JSON不是列表格式时

    Example:
        >>> json_str = '[{"title": "test", "doc": "content"}]'
        >>> results = parse_json_to_model_list(json_str, AworldSearch)
    """
    try:
        # 清理可能的代码块标记
        cleaned_json = json_data.strip()

        # 移除开头的代码块标记
        if cleaned_json.startswith('```json'):
            cleaned_json = cleaned_json[7:]  # 移除 '```json'
        elif cleaned_json.startswith('```'):
            cleaned_json = cleaned_json[3:]   # 移除 '```'

        # 移除结尾的```标记
        if cleaned_json.endswith('```'):
            cleaned_json = cleaned_json[:-3]

        # 再次清理空白字符
        cleaned_json = cleaned_json.strip()

        # 解析JSON字符串
        parsed_data = json.loads(cleaned_json)

        # 确保解析结果是列表
        if not isinstance(parsed_data, list):
            raise ValueError(f"Expected JSON array, got {type(parsed_data)}")

        # 将列表中的每个字典转换为模型实例
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
    从文本中提取JSON内容，去除前后的非JSON内容
    
    Args:
        text (str): 包含JSON的原始文本
        
    Returns:
        str: 提取出的纯净JSON字符串
        
    Example:
        >>> text = '''基于对执行详情的全面分析，我现在可以对这个解决方案进行验证：
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
