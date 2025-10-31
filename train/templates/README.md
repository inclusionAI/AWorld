# Template System

The Template system is used during Rollout. By leveraging this template tool, you can perform fine-grained custom processing on LLM inputs in the apply_chat_template method of RolloutLLMProvider. The Template code is derived from the AgentFly project.

For more details, please visit https://github.com/Agent-One-Lab/AgentFly. Thanks to the AgentFly team for contributing this excellent tool!

## Template Usage
### Prompt generation
```python
from train.templates import get_template

template = get_template("llama-3.2")
prompt, _, _ = template.render(messages = [
    {"role": "user", "content": "What is the capital of France?"},
    {"role": "assistant", "content": "The capital of France is Paris."},
    {"role": "user", "content": "Tell me more about Paris."}
], add_generation_prompt=False)
print(prompt)
```

### Tokenization in RolloutLLMProvider
```python
from train.templates import get_template

class MyRolloutLLMProvider(RolloutLLMProvider):

    ...

    def apply_chat_template(self, messages: List[Dict[str, str]]) -> List[int]:
        template = get_template(self.model_name)
        token_ids = template.encode(messages = messages, tokenizer=self.tokenizer, add_generation_prompt=False)
        return token_ids.input_ids


```
