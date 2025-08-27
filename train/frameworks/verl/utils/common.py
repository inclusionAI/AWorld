import asyncio
from typing import List, Dict, Any
from verl.experimental.agent_loop.agent_loop import AgentLoopBase, AgentLoopOutput, AgentLoopMetrics


async def to_agent_loop_output(messages: List[Dict[str, Any]], response_length: int) -> AgentLoopOutput:
    """Convert messages to AgentLoopOutput.

    Args:
        messages (List[Dict[str, Any]]): List of messages in OpenAI request format.
        response_length (int): Max length of response.

    Returns:
        AgentLoopOutput: agent loop output trajectory used for training.
    """
    if not messages:
        return AgentLoopOutput(
            prompt_ids=[],
            response_ids=[],
            response_mask=[],
            num_turns=0,
            metrics={},
        )

    num_turns = 0
    for i in range(len(messages)):
        if messages[i].get("role") == "system":
            continue
        # parallel tool calls are in single turn
        if i == 0 or messages[i].get("role") != messages[i - 1].get("role"):
            num_turns += 1

    prompt_ids = []
    response_ids = []
    response_mask = []
    chat_list = []
    loop = asyncio.get_running_loop()
    # system_prompt_prefix_ids = self.tokenizer.apply_chat_template([{}], add_generation_prompt=False, tokenize=True)
    i = 0
    try:
        while i < len(messages):
            if messages[i].get("role") == "system":
                chat_list.append(messages[i])
                i += 1
                continue
            # initial chat completion
            if messages[i].get("role") == "user":
                if (i == 0 or messages[i - 1].get("role") == "system"):
                    chat_list.append(messages[i])
                    prompt_ids = await loop.run_in_executor(
                        None,
                        lambda: self.tokenizer.apply_chat_template(
                            chat_list,
                            tools=self.agent.tools,
                            add_generation_prompt=True,
                            tokenize=True,
                        ),
                    )
                    chat_list = []
                    i += 1
                    continue
                else:
                    chat_list.append(messages[i])
                    cur_response_ids = await loop.run_in_executor(
                        None,
                        lambda: self.tokenizer.apply_chat_template(
                            chat_list,
                            add_generation_prompt=True,
                            tokenize=True,
                        ),
                    )
                    chat_list = []
                    response_ids += cur_response_ids
                    response_mask += [0] * len(cur_response_ids)
                    i += 1
                    continue
            # assistant message
            if messages[i].get("role") == "assistant":
                chat_list.append(messages[i])
                cur_response_ids = await loop.run_in_executor(
                    None,
                    lambda: self.tokenizer.apply_chat_template(
                        chat_list,
                        tools=self.agent.tools,
                        add_generation_prompt=True,
                        tokenize=True,
                    ),
                )
                chat_list = []
                response_ids += cur_response_ids
                response_mask += [1] * len(cur_response_ids)
                i += 1
                continue
            # follow up chat completion with tool response:
            if messages[i].get("role") == "tool":
                last_assistant_message = messages[i - 1]
                chat_list.append(last_assistant_message)
                token_assistant = await loop.run_in_executor(
                    None,
                    lambda: self.tokenizer.apply_chat_template(
                        chat_list,
                        tools=self.agent.tools,
                        add_generation_prompt=True,
                        tokenize=True,
                    ),
                )
                while i < len(messages) and messages[i].get("role") == "tool":
                    chat_list.append(messages[i])
                    i += 1
                token_assistant_tool = await loop.run_in_executor(
                    None,
                    lambda: self.tokenizer.apply_chat_template(
                        chat_list,
                        tools=self.agent.tools,
                        add_generation_prompt=True,
                        tokenize=True,
                    ),
                )
                tool_response_ids = token_assistant_tool[len(token_assistant):]
                chat_list = []
                response_ids += tool_response_ids
                response_mask += [0] * len(tool_response_ids)
    except Exception as e:
        raise Exception(f"Failed to convert messages to agentloop_output: {messages}")

    max_response_length = min(response_length, len(response_ids))
    output = AgentLoopOutput(
        prompt_ids=prompt_ids,
        response_ids=response_ids[:max_response_length],
        response_mask=response_mask[:max_response_length],
        num_turns=num_turns,
        metrics={},
    )
    return output