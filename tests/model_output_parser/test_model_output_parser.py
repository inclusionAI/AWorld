# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import unittest
import sys
from typing import Any, Dict

from aworld.agents.llm_agent import LlmOutputParser
from aworld.core.model_output_parser.base_content_parser import BaseContentParser
from aworld.core.model_output_parser.model_output_parser import ModelOutputParser
from aworld.models.model_response import ModelResponse


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
        resp.reasoning_content = "mock"
        return resp


class ConcreteModelOutputParser(ModelOutputParser[ModelResponse, Dict[str, Any]]):
    async def parse(self, content: ModelResponse, **kwargs) -> Dict[str, Any]:
        result = {}
        for parser_type, parser in self._parsers.items():
            result[parser_type] = parser.parse(content, **kwargs)
        return result


class TestModelOutputParser(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.parser1 = MockParser("reasoning", "thought process")
        self.parser2 = MockParser("code", "print('hello')")

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

    async def test_parse(self):
        parser = LlmOutputParser()

        parser.register_parser(self.parser1)
        mock_response = ModelResponse(
            id="mock",
            model="mock_model",
            content="""
                    Here is content.<tool_call>{"name": "tool_1", "arguments": {"content": "mocking_tool_arg"}}</tool_call>
                    """
        )
        result = await parser.parse(mock_response, agent_id="mock_agent", use_tools_in_prompt=True)
        print(f"result after parsed: {result}")

        self.assertEqual(mock_response.reasoning_content, "mock")
        self.assertTrue(result.is_call_tool)
        self.assertIsNotNone(result.actions[0].tool_call_id)
        self.assertIsNotNone(result.actions[0].params)




if __name__ == '__main__':
    unittest.main()

