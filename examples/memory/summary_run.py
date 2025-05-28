from aworld.core.memory import MemoryConfig
from aworld.memory.main import MemoryFactory

SUMMARY_PROMPT = """
You are a helpful assistant that extracts relevant information from the content within the <tobesummary> tag to help solve long context limitations.

Your task is to extract content that is relevant to the current user's submitted task from a long text passage.

Guidelines:
- 1. Carefully analyze the content within <tobesummary> tags to understand the main topics and context
- 2. Extract only the information that is directly relevant to the current user's task or query
- 3. Ensure the extracted information is concise and clear, removing redundant or irrelevant details
- 4. Preserve the original meaning and important details of the extracted content
- 5. If multiple relevant sections exist, organize them logically
- 6. Return the extracted content in a structured and readable format

Focus on maintaining the essential information while significantly reducing the overall length to fit within context limitations.

<tobesummary>{context}</tobesummary>
"""

if __name__ == '__main__':
    memory_config = MemoryConfig(provider="mem0",
                                 enable_summary=True,
                                 summary_single_context_length=5000,
                                 summary_prompt=SUMMARY_PROMPT
                                 )
    mem = MemoryFactory.from_config(memory_config)


    import json
    from aworld.core.memory import MemoryItem

    # Read and parse the history.jsonl file
    with open('./history.jsonl', 'r') as f:
        for line in f:
            memory_item = MemoryItem.model_validate_json(line)
            mem.add(memory_item)
    

    filter = {'agent_id': 'gaia_agent_b9de35', 'task_id': 'b9dace723b7511f08128627fc1420302'}


    to_be_summary = MemoryItem(
        content=f"{open('./to_be_summary.txt', 'r').read()}",
        memory_type="message",
        metadata={'agent_id': 'gaia_agent_b9de35',
                  'agent_name': 'gaia_agent',
                  'role': 'tool',
                  'session_id': None,
                  'task_id': 'b9dace723b7511f08128627fc1420302',
                  'tool_call_id': 'fc-61e15a24-3186-4ab7-8e53-0bdf0e7d2ec1',
                  'user_id': None
        }
    )
    result = mem.summary_content(to_be_summary, filters=filter, last_rounds=10)
    print(result)