
# Add the project root to Python path
import json
import os
from pathlib import Path
import random
import sys

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from aworld.core.agent.swarm import Swarm
from aworld.runner import Runners

from aworld.agents.llm_agent import Agent
from aworld.config.conf import AgentConfig, ContextRuleConfig, ModelConfig
from tests.base_test import BaseTest
from aworld.core.context.base import Context
from aworld.core.context.prompts.dynamic_variables import create_multiple_field_getters, create_simple_field_getter, format_ordered_dict_json, get_field_values_from_list, get_simple_field_value, get_value_by_path
from aworld.core.context.prompts.string_prompt_formatter import StringPromptFormatter, PromptTemplate
from aworld.core.context.prompts.formatters import TemplateFormat

class TestPromptTemplate(BaseTest):
    def __init__(self):
        """Set up test fixtures"""
        self.mock_model_name = "qwen/qwen3-1.7b"
        self.mock_base_url = "http://localhost:1234/v1"
        self.mock_api_key = "lm-studio"
        os.environ["LLM_API_KEY"] = self.mock_api_key
        os.environ["LLM_BASE_URL"] = self.mock_base_url
        os.environ["LLM_MODEL_NAME"] = self.mock_model_name

    def init_agent(self, system_prompt: str, agent_prompt: str):
        conf = AgentConfig(
            llm_config=ModelConfig(
                llm_model_name=self.mock_model_name,
                llm_base_url=self.mock_base_url,
                llm_api_key=self.mock_api_key
            )
        )
        return Agent(
            conf=conf,
            name="my_agent" + str(random.randint(0, 1000000)),
            system_prompt=system_prompt,
            agent_prompt=agent_prompt,
        )

    def run_agent(self, input, agent: Agent):
        swarm = Swarm(agent, max_steps=1)
        return Runners.sync_run(
            input=input,
            swarm=swarm
        )

    def test_dynamic_variables(self):
        context = Context()
        context.context_info.update({"task": "chat"})
        
        # Test dot separator
        value_dot = get_value_by_path(context, "context_info.task")
        assert "chat" == value_dot
        
        # Test slash separator
        value_slash = get_value_by_path(context, "context_info/task")
        assert "chat" == value_slash
        
    def test_formatted_field_getter(self):
        context = Context()
        value = {"steps": [1, 2, 3]}
        context.trajectories.update(value)
    
        getter = create_simple_field_getter(field_path="trajectories", default="default_value")
        result = getter(context=context)
        assert "steps" in value
        # test default format function
        assert "OrderedDict" not in result
    
        # Test formatted field getter with processor
        getter = create_simple_field_getter(field_path="trajectories", default="default_value", processor=format_ordered_dict_json)
        result = getter(context=context)
        assert "steps" in result
    
    def test_multiple_field_getters(self):
        context = Context()
        context.context_info.update({"task": "chat"})
        context.trajectories.update({"steps": [1, 2, 3]})
    
        field_paths = ["context_info.task", "trajectories.steps"]
        result = get_field_values_from_list(context=context, field_paths=field_paths)
        assert result["context_info_task"] == "chat"
        assert result["trajectories_steps"] == "[1, 2, 3]"
    
    def test_string_prompt_template(self):
        # Use proper dot notation for nested field access
        template = StringPromptFormatter.from_template("Hello {{name}}, welcome to {{place}}! Task: {{task}} Age: {{age}}",
                                                      partial_variables={"age": "1"})
        assert "name" in template.input_variables
        assert "place" in template.input_variables
        assert "task" in template.input_variables
    
        context = Context()
        context.context_info.update({"task": "chat"})
        
        # Pass task as a direct parameter since template expects it
        result = template.format(context=context, name="Alice", place="AWorld", task="chat")
        assert result == "Hello Alice, welcome to AWorld! Task: chat Age: 1"
    
    def test_enhanced_field_values_basic(self):
        context = Context()
        context.context_info.update({"task": "chat"})
        
        # Test retrieving both time variables and context fields
        result = get_field_values_from_list(
            context=context,
            field_paths=["current_time", "context_info.task"],
            default="not_found"
        )
        
        # Verify context field retrieved
        assert result["context_info_task"] == "chat"
        
        # Verify time variable retrieved (should be in HH:MM:SS format)
        assert ":" in result["current_time"]
        assert len(result["current_time"].split(":")) == 3

    def check_messages_0(self, messages):
        assert len(messages) == 2
        # print("messages: ", messages)
        assert messages[0]['content'] == "You are a helpful assistant."
        # print(messages)
        assert "<system_instruction>" in messages[1]['content']

    def check_messages_1(self, messages):
        assert len(messages) == 2
        # print("messages: ", messages)
        assert messages[0]['content'] == "You are a helpful assistant."
        # print(messages)
        assert messages[1]['content'] == "hello world"

    def check_messages_2(self, messages):
        assert len(messages) == 2
        assert messages[0]['content'] == "You are a helpful assistant."
        print(messages)
        assert messages[1]['content'] == "Mike {{age}} play"

if __name__ == "__main__":
    test = TestPromptTemplate()
    test.test_dynamic_variables()
    test.test_formatted_field_getter()
    test.test_multiple_field_getters()
    test.test_string_prompt_template()
    test.test_enhanced_field_values_basic()
    

