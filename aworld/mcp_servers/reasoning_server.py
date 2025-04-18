"""
Reasoning MCP Server

This module provides MCP (Model-Controller-Processor) server functionality for reasoning.
It includes tools for accessing OpenAI o3-mini model's ability to solve complex problems.

Main functions:
- complex_reasoning: Performs complex problem reasoning using OpenAI o3-mini model given a specific question.
"""

import os
import traceback

from pydantic import Field

from aworld.config.conf import AgentConfig
from aworld.logs.util import logger
from aworld.mcp_servers.utils import parse_port, run_mcp_server
from aworld.models.llm import get_llm_model


class ReasoningServer:
    """
    Reasoning Server class for complex problem reasoning.
    For math and code contest problem, this class utilizes OpenAI o3-mini model to obtain superior performance.
    For riddle and puzzle, the result of this class could be fully trusted.
    """

    _instance = None
    _llm = None
    _llm_config = None

    def __new__(cls):
        """Implement singleton pattern"""
        if cls._instance is None:
            cls._instance = super(ReasoningServer, cls).__new__(cls)
            cls._instance._init_server()
        return cls._instance

    def _init_server(self):
        """Initialize the reasoning server"""
        self._llm_config = AgentConfig(
            llm_provider="openai",
            llm_model_name="o3-mini",
            llm_base_url=os.getenv("LLM_BASE_URL", ""),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
        )
        self._llm = get_llm_model(self._llm_config)
        logger.info("ReasoningServer initialized")

    @classmethod
    def get_instance(cls):
        """Get the singleton instance of ReasoningServer"""
        if cls._instance is None:
            return cls()
        return cls._instance

    @classmethod
    def complex_problem_reasoning(
        cls,
        question: str = Field(
            description="The input question for complex problem reasoning, such as math and code contest problem",
        ),
        original_task: str = Field(
            default="",
            description="The original task description. This argument could be fetched from the <task>TASK</task> tag",
        ),
    ) -> str:
        """
        Perform complex problem reasoning using OpenAI o3-mini model.

        Args:
            question: The input question for complex problem reasoning
            original_task: The original task description (optional)

        Returns:
            str: The reasoning result from the model
        """
        # Get the singleton instance and ensure server is initialized
        instance = cls.get_instance()

        try:
            # Prepare the prompt with both the question and original task if provided
            prompt = question
            if original_task:
                prompt = f"Original Task: {original_task}\n\nQuestion: {question}"

            # Call the LLM model for reasoning
            response = instance._llm.provider.completion(
                model="o3-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert at solving complex problems including math, code contests, riddles, and puzzles. Provide detailed step-by-step reasoning and a clear final answer.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,  # Lower temperature for more deterministic reasoning
            )

            # Extract the reasoning result
            reasoning_result = response.content

            logger.info(f"Complex reasoning completed successfully")
            return reasoning_result

        except Exception as e:
            logger.error(
                f"Error in complex problem reasoning: {traceback.format_exc()}"
            )
            return f"Error performing reasoning: {str(e)}"


if __name__ == "__main__":
    port = parse_port()

    reasoning_server = ReasoningServer.get_instance()
    logger.info("ReasoningServer initialized and ready to handle requests")

    run_mcp_server(
        "Reasoning Server",
        funcs=[reasoning_server.complex_problem_reasoning],
        port=port,
    )
