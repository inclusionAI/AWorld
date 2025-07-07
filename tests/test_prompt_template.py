
# Add the project root to Python path
import json
from pathlib import Path
import sys


project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


from aworld.core.context.base import Context
from aworld.core.context.prompts.dynamic_variables import create_multiple_field_getters, create_simple_field_getter, format_ordered_dict_json, get_field_values_from_list, get_simple_field_value, get_value_by_path
from aworld.core.context.prompts.string_prompt_template import StringPromptTemplate, PromptTemplate
from aworld.core.context.prompts.formatters import TemplateFormat

def test_dynamic_variables():
    context = Context()
    context.context_info.update({"task": "chat"})
    
    # Test dot separator
    value_dot = get_value_by_path(context, "context_info.task")
    assert "chat" == value_dot
    
    # Test slash separator
    value_slash = get_value_by_path(context, "context_info/task")
    assert "chat" == value_slash
    
def test_formatted_field_getter():
    context = Context()
    value = {"steps": [1, 2, 3]}
    context.trajectories.update(value)

    getter = create_simple_field_getter(field_path="trajectories", default="default_value")
    result = getter(context=context)
    assert "steps" in value

    # Test formatted field getter with processor
    getter = create_simple_field_getter(field_path="trajectories", default="default_value", processor=format_ordered_dict_json)
    result = getter(context=context)
    assert json.dumps(value, ensure_ascii=False, indent=None) == result

def test_multiple_field_getters():
    context = Context()
    context.context_info.update({"task": "chat"})
    context.trajectories.update({"steps": [1, 2, 3]})

    field_paths = ["context_info.task", "trajectories.steps"]
    result = get_field_values_from_list(context=context, field_paths=field_paths)
    assert result["context_info_task"] == "chat"
    assert result["trajectories_steps"] == "[1, 2, 3]"

def test_string_prompt_template():
    # Use proper dot notation for nested field access
    template = StringPromptTemplate.from_template("Hello {name}, welcome to {place}! Task: {task} Age: {age}",
                                                  partial_variables={"age": "1"})
    assert "name" in template.input_variables
    assert "place" in template.input_variables
    assert "task" in template.input_variables

    context = Context()
    context.context_info.update({"task": "chat"})
    
    # Pass task as a direct parameter since template expects it
    result = template.format(context=context, name="Alice", place="AWorld", task="chat")
    assert result == "Hello Alice, welcome to AWorld! Task: chat Age: 1"

if __name__ == "__main__":
    test_dynamic_variables()
    test_formatted_field_getter()
    test_multiple_field_getters()
    test_string_prompt_template()
    print("✅ test_prompt_template.py All tests passed!")
    

