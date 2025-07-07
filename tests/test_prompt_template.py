
from aworld.core.context.base import Context
from aworld.core.context.prompts.dynamic_variables import create_simple_field_getter
from aworld.core.context.prompts.string_prompt_template import StringPromptTemplate, PromptTemplate
from aworld.core.context.prompts.formatters import TemplateFormat

def test_dynamic_variables():
    context = Context()
    value = {"steps":[1, 2, 3]}
    context.trajectories.update(value)

    getter = create_simple_field_getter("trajectories", context)
    formatted_value = getter(context = context)
    assert str(value) in formatted_value


def test_string_prompt_template():
    # 1. 基本功能测试
    template = StringPromptTemplate.from_template("Hello {name}, welcome to {place}!")
    assert "name" in template.input_variables
    assert "place" in template.input_variables
    
    result = template.format(name="Alice", place="AWorld")
    assert result == "Hello Alice, welcome to AWorld!"
    print("✓ Basic functionality test passed")
    
    # 2. 使用Context对象
    context = Context()
    context.context_info.update({"task": "chat"})
    
    context_template = StringPromptTemplate.from_template("Task: {task}\nUser: {user_input}")
    result = context_template.format(context=context, task="chat", user_input="Hello!")
    assert "Task: chat" in result
    assert "User: Hello!" in result
    print("✓ Context integration test passed")
    
    # 3. 预设变量功能
    partial_template = StringPromptTemplate.from_template(
        "System: {system_prompt}\nUser: {user_input}",
        partial_variables={"system_prompt": "You are helpful."}
    )
    assert "user_input" in partial_template.input_variables
    assert "system_prompt" not in partial_template.input_variables
    
    result = partial_template.format(user_input="Hi!")
    assert "System: You are helpful." in result
    print("✓ Partial variables test passed")
    
    # 4. 模板组合
    template1 = StringPromptTemplate.from_template("Hello {name}!")
    template2 = StringPromptTemplate.from_template(" Welcome to {place}.")
    combined = template1 + template2
    
    result = combined.format(name="Bob", place="AWorld")
    assert result == "Hello Bob! Welcome to AWorld."
    print("✓ Template combination test passed")
    
    # 5. PromptTemplate别名
    alias_template = PromptTemplate.from_template("Test {value}")
    assert isinstance(alias_template, StringPromptTemplate)
    result = alias_template.format(value="success")
    assert result == "Test success"
    print("✓ PromptTemplate alias test passed")
    
    print("🎉 All StringPromptTemplate tests passed!")


if __name__ == "__main__":
    test_dynamic_variables()
    test_string_prompt_template()
    

