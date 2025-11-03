import unittest
from train.templates.templates import get_template


class TemplateTest(unittest.IsolatedAsyncioTestCase):

    async def test_template_render(self):
        template = get_template("qwen2.5")
        prompt, _, _ = template.render(messages=[
            {"role": "user", "content": "What is the capital of France?"},
            {"role": "assistant", "content": "The capital of France is Paris."},
            {"role": "user", "content": "Tell me more about Paris."}
        ], add_generation_prompt=False)
        print(prompt)

        prompt, _, _ = template.render(messages=[
            {"role": "user", "content": "What is the capital of France?"},
            {"role": "assistant", "content": "The capital of France is Paris."},
            {"role": "user", "content": "Tell me more about Paris."}
        ], add_generation_prompt=True)
        assert prompt.endswith("<|im_start|>assistant\n")

    async def test_template_render_with_tool(self):
        template = get_template("llama-3.2")
        tools = [
            {
                "function": {
                    "name": "get_weather",
                    "description": "Get weather information for a city",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "city": {"type": "string", "description": "City name"}
                        },
                        "required": ["city"]
                    }
                }
            }
        ]
        prompt, _, _ = template.render(messages=[
            {"role": "user", "content": "What is the weather like in Paris?"}
        ], add_generation_prompt=False, tools=tools)
        print(prompt)

    def test_render_with_mask(self):
        template = get_template("llama-3.2")
        prompt, _, _ = template.render_with_mask(messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is the capital of France?"},
            {"role": "assistant", "content": "The capital of France is Paris."},
            {"role": "user", "content": "Tell me more about Paris."}
        ], add_generation_prompt=False)
        print(prompt)
