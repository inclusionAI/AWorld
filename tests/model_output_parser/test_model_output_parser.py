# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import unittest
import sys
from typing import Any, Dict

from aworld.agents.llm_agent import Agent
from aworld.config import ModelConfig, AgentConfig
from aworld.core.model_output_parser.base_content_parser import BaseContentParser
from aworld.core.model_output_parser.default_parsers import ReasoningParser
from aworld.models.llm import ModelResponseParser
from aworld.models.model_response import ModelResponse
from aworld.runner import Runners


# os.environ["LLM_MODEL_NAME"] = "YOUR_LLM_MODEL_NAME"
# os.environ["LLM_BASE_URL"] = "YOUR_LLM_BASE_URL"
# os.environ["LLM_API_KEY"] = "YOUR_LLM_API_KEY"

class MockParser(BaseContentParser):
    def __init__(self, parser_type: str, result: Any):
        self._parser_type = parser_type
        self._result = result
        self.call_count = 0

    @property
    def parser_type(self) -> str:
        return self._parser_type

    async def parse(self, resp: ModelResponse, **kwargs) -> Any:
        self.call_count += 1
        resp.reasoning_content = self._result
        return resp


class ConcreteModelOutputParser(ModelResponseParser):
    async def parse(self, content: ModelResponse, **kwargs) -> Dict[str, Any]:
        result = {}
        for parser_type, parser in self._parsers.items():
            result[parser_type] = parser.parse(content, **kwargs)
        return result


class MyReasoningParser(ReasoningParser):
    async def parse(self, resp: ModelResponse, **kwargs) -> Any:
        content, thinking_content = await self.extract_thinking_content(resp.content)
        resp.reasoning_content = thinking_content
        resp.content = content
        return resp


class TestModelOutputParser(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.parser1 = MockParser("reasoning", "thought process")
        self.parser2 = MockParser("code", """
        ```python
        print('hello')
        """)

    def test_init_with_parsers(self):
        parser = ConcreteModelOutputParser(parsers=[self.parser1, self.parser2])
        self.assertEqual(parser.get_parser("reasoning"), self.parser1)
        self.assertEqual(parser.get_parser("code"), self.parser2)

    def test_register_parser(self):
        parser = ConcreteModelOutputParser()
        parser.register_parser(self.parser1)
        self.assertEqual(parser.get_parser("reasoning"), self.parser1)

        # Test overwrite
        new_parser1 = MockParser("reasoning", "new thought")
        parser.register_parser(new_parser1)
        self.assertEqual(parser.get_parser("reasoning"), new_parser1)

    def test_get_parsers(self):
        parser = ConcreteModelOutputParser(parsers=[self.parser1])
        parser.register_parser(self.parser2)

        parsers = parser.get_parsers()
        self.assertEqual(len(parsers), 2)
        self.assertEqual(parsers["reasoning"], self.parser1)
        self.assertEqual(parsers["code"], self.parser2)

    async def test_default_parses(self):
        parser = ModelResponseParser(enable_default_parsers=True)

        mock_response = ModelResponse(
            id="mock",
            model="mock_model",
            content="""
                    <think>Here is reasoning content.</think>
                    Here is content.<tool_call>{"name": "tool_1", "arguments": {"content": "mocking_tool_arg"}}</tool_call>
                    codecodecode
                    ```python
                    print('hello')
                    ```
                    Add some json content.
                    ```json
                    {
                        "mock_key": "mock_value"
                    }
                    ```
                    """
        )
        result = await parser.parse(mock_response, agent_id="mock_agent", use_tools_in_prompt=True)
        print(f"result after parsed: {result}")

        self.assertIsNotNone(result.tool_calls)
        self.assertEqual(result.reasoning_content, "Here is reasoning content.")
        self.assertIsNotNone(result.structured_output)
        self.assertEqual(len(result.structured_output["parsed_json"]), 1)
        self.assertDictEqual(result.structured_output["parsed_json"][0], {"mock_key": "mock_value"})
        self.assertEqual(len(result.structured_output["code_blocks"]), 1)

    async def test_customized_parsers(self):
        new_reasoning_parser = MockParser("reasoning", "mocking reasoning")
        parser = ModelResponseParser(parsers=[new_reasoning_parser], enable_default_parsers=False)

        mock_response = ModelResponse(
            id="mock",
            model="mock_model",
            content="""
                    Here is content.<tool_call>{"name": "tool_1", "arguments": {"content": "mocking_tool_arg"}}</tool_call>
                    ```python
                    print('hello')
                    ```
                    """
        )
        result = await parser.parse(mock_response, agent_id="mock_agent", use_tools_in_prompt=True)
        print(f"result after parsed: {result}")

        self.assertIsNotNone(result.tool_calls)
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.reasoning_content, "mocking reasoning")

    async def test_agent_llm_parse(self):
        agent_config = AgentConfig(
            llm_config=ModelConfig(
                llm_response_parser=ModelResponseParser(parsers=[MyReasoningParser()], enable_default_parsers=True)
            )
        )
        my_agent = Agent(
            conf=agent_config,
            name="my_agent",
            system_prompt="You are a helpful assistant. Think before answer."
        )
        res = await Runners.run(
            input="How many 'r's are there in strawberry?",
            agent=my_agent
        )
        print(f"get res: {res.answer}")
        self.assertTrue("<think>" not in res.answer)



if __name__ == '__main__':
    unittest.main()

