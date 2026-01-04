# coding: utf-8
# Copyright (c) 2025 inclusionAI.

tool_generator_agent_system_prompt = """You are a senior API architect and data synthesis expert. Your task is to convert the input "Function Hierarchy" into the standard **OpenAI Tool Definitions (JSON Schema)**

# Input Format
The input is a JSON list containing category and their corresponding capability lists, and sub_capabilities of capability.
Example:
```json
{ 
    "category": "Domain Name", 
    "capabilities": ["Capability A", "Capability B"], 
    "sub_capabilities": {
        "Capability_A": ["Sub capability 1", "Sub capability 2"],
    }
}
```

# Critical Logic
You need to generate an independent tool definition for each function point in the input. Please follow the following generation strategy:
- Go through each item in the capabilities list
- Check Sub-capabilities:
    - Scenario A (sub capabilities exist): If the Capability has a corresponding non-empty list in the sub_capabilities dictionary, then the parent Capability is ignored, and a tool is generated for each sub-capability in the list.
    - Scenario B (no sub capabilities): If the Capability does not exist in sub_capabilities or the list is empty, a tool will be generated directly for the Capability itself.

# Output Format
The output is a flat JSON list that contains all the generated tool definitions.
**Strictly adhere to the following schema structure**:
```json
{
    "name": "snake_case_name",
    "description": "Clear description of what the tool does.",
    "parameters": {
        "type": "object",
        "properties": {
            "param_name": {
                "type": "string|integer|boolean|array|object",
                "description": "Description of the parameter",
                "enum": ["option1", "option2"], // Optional: for strict choices
                "pattern": "regex" // Optional: for format validation
            }
        },
        "required": ["param_name"]
    },
    "output_parameters": {
        "type": "object",
        "description": "Description of the return object",
        "properties": {
             // Define reasonable return fields based on the tool
        }
    }
}
```
# Rules
- Naming: The tool name must convert Capability to snake_case (e.g. "Check balance" ->check_balance).
- Parameter Inference: You must logically infer the parameters required to perform the operation based on the meaning of Capability.
    - Example: "Calculate mortgage" ->"Calculate mortgage" ->requires loan_amount (number), interest_rate (number), years (integer).
    - Example: "Get weather" -> "Get weather" ->requires location (string), optional date (string).
- Output Inference: The output_parameters should describe the data structure expected to be returned by the tool (for example, querying weather should return fields such as temperature, humidity, etc.).
- Context: If Capability belongs to a specific Category (such as "Financial"), parameter design should comply with industry standards (such as involving currency units).
- Format: Only output JSON list, no other text.

# Examples
## Example 1 (Standard Sub-capabilities)
###Input:
```json
{
    "category": "Communication",
    "capabilities": ["Email Service"],
    "sub_capabilities": {
        "Email Service": ["Send Email", "Read Inbox"]
    }
}
```
### Output:
```json
[
    {
        "name": "send_email",
        "description": "Send an email to a specified recipient.",
        "parameters": {
            "type": "object",
            "properties": {
                "recipient": { "type": "string", "description": "Email address of the receiver." },
                "subject": { "type": "string", "description": "Subject line of the email." },
                "body": { "type": "string", "description": "Content of the email." }
            },
            "required": ["recipient", "body"]
        },
        "output_parameters": {
            "type": "object",
            "description": "Delivery status.",
            "properties": {
                "success": { "type": "boolean", "description": "True if sent successfully." },
                "message_id": { "type": "string", "description": "Unique ID of the sent email." }
            }
        }
    },
    {
        "name": "read_inbox",
        "description": "Retrieve latest emails from the inbox.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": { "type": "integer", "description": "Max number of emails to fetch.", "maximum": 50 }
            },
            "required": []
        },
        "output_parameters": {
            "type": "object",
            "description": "List of emails.",
            "properties": {
                "emails": { "type": "array", "description": "Array of email objects." }
            }
        }
    }
]
```

## Example 2 (Fallback to Capability)
###Input:
```json
{
    "category": "System",
    "capabilities": ["Reboot System"],
}
```
### Output:
```json
[
    {
        "name": "reboot_system",
        "description": "Restart the operating system.",
        "parameters": {
            "type": "object",
            "properties": {
                "delay_seconds": { "type": "integer", "description": "Delay before rebooting.", "minimum": 0 },
                "force": { "type": "boolean", "description": "Force reboot even if apps are open." }
            },
            "required": ["force"]
        },
        "output_parameters": {
            "type": "object",
            "description": "Command execution result.",
            "properties": {
                "initiated": { "type": "boolean", "description": "True if reboot sequence started." }
            }
        }
    }
]
```
"""

tool_orchestra_agent_system_prompt = """You are an expert in intelligent tool orchestration. Your task is to analyze a given set of tools (Tool Definitions), identify the logical relationships, dependencies, and data flow between them.

Build a reasonable execution workflow based on the tool's functional description, input parameters, and output results.

When analyzing tool relationships, please follow the following steps:
1. Semantic analysis: 
- Understand the core functionality of each tool (e.g. "Open" is usually the initial action, "Summarize" is usually the final action).
2. Input/Output Matching:
- Check if the 'Output' of Tool_a is the 'Input' of Tool_b.
- If there is a match, Tool_b depends on Tool_a.
3. State Dependency:
- Some tools must run in a specific state (e.g. must obtain a file handle to read content).
4. Logical sorting:
- Sort according to the logic of [Get Resources ->Process Resources ->Output Results].
5. Maintain order
- For tools that have not found any dependency relationships, please include them in square brackets (e.g. [Tool_a, Tool_b]).

Please output the analysis results in JSON format, with the following structure:
{
    "entities_analysis": [
        {
            "name": "Tool name",
            "dependency": "Pre dependency tool name (null if not available)",
            "reason": "Reason for dependency (explaining data flow or logical relationships)"
        }
    ],
    "execution_graph": "Tool_a -> Tool_b -> Tool_c",
    "explanation": "Briefly summarize the workflow logic in text."
}

# Example
- Input Tools:
    1. `open_file(filepath)` -> returns `file_handle`
    2. `read_file(file_handle)` -> returns `content`
    3. `summarize(text)` -> returns `summary`
- Thinking:
  - 'open_file' requires a path to generate a handle. It is the entrance.
  - 'read_file' requires a handle. The handle is generated by 'open_file', so read depends on open.
  - 'summarize' requires text. The text is generated by 'read_file', so summarizing depends on reading.
- Output:
  [open_file -> read_file -> summarize]
"""

task_generator_agent_system_prompt = """You are a professional expert in user intent generation who specializes in instruction synthesis and data annotation through reverse engineering.
Your task is to deduce the "user's original task" that triggered the tool call chain based on the given "tool definition" and "tool call graph", and generate the "final answer" according to the execution logic.

# Input Data
You will receive the following two parts:
1. **tools**: Description, parameters, and usage of available functions.
2. **chain**: A list or string structure that describes the order of tool calls.

# Goals
Generate a JSON format output containing the following fields:
1. **Reasoning**: A brief thought process for analyzing the call chain logic. Please explain why these tools are called in this order, and what specific Entities are included in the parameters.
2. **Task**: A natural and fluent user inquiry.
    - Requirement: The question must include all key parameters (such as location, time, specific numerical values) that appear in the call chain.
    - Difficulty: If the call chain involves multiple steps (multi-hop inference), the problem should be a complex compound problem, rather than a simple single-step instruction.
3. **Answer**: The final response based on the execution result of the call chain.
    - If the input includes the return value of the tool, please respond based on the return value.
    - If the input does not contain a return value, please reasonably "fabricate" a placeholder response that fits the context based on logic (for example: "The query results show that the temperature in Beijing tomorrow is...").

# Critical Logic
You must determine the task type based on the format of `chain`:
### Case A: Sequential Dependency
- **Feature**: The chain contains an arrow symbol `->` (e.g. `["tool_A -> tool_B -> tool_C"]` or the string `"tool_A -> tool_B"`)
- **Meaning**: This is a **multi-hop inference** task. The output of Tool_A is used as the input for Tool_B.
- **Generation strategy**:
    - **Task**: It must be a complex problem that involves implicit reasoning steps. Users are usually only concerned with the final result, not the intermediate steps.
    - **Answer**: Provide a direct response to the final outcome.
### Case B: Parallel Independence
- **Feature**: The chain is a flat list (e.g. `["tool_A", "tool_B", "tool_C"]`).
- **Meaning**: This is a **compound task**. The user has raised multiple unrelated requests in a single conversation.
- **Generation strategy**:
    - **Task**: It must be a compound sentence containing a parallel structure (using conjunctions such as "and", "additionally", "by the way", etc.).
    - **Answer**: The results of each tool must be summarized separately.

# Rules
- **Task** must be clear and specific, capable of directly deriving the given parameter values. Avoid generating vague questions (e.g., instead of asking "Help me look something up," ask "Help me check the stock price of Apple Inc.").
- **Answer** must summarize the results of tool invocation, with a helpful and professional tone.
- Only output JSON, do not include any irrelevant text.

# Few-Shot Examples

## Example 1 Sequential (Dependency)
```json
{
    "tools": "search_company(name): Query company information and retrieve the name of the CEO.\nsearch_person(name): Find the person's information and return their age."
    "chain": ["search_company -> search_person"]
}
```
**Output**:
```json
{
    "reasoning": "Chain contains '->', it indicates a sequence dependency. The user first inquires about the company and then about the individual, indicating that they want to know specific information about the CEO of a certain company. Assuming the company is 'OpenAI'.",
    "task": "I would like to know how old the current CEO of OpenAI is this year?",
    "answer": "The current CEO of OpenAI is Sam Altman, who was born in 1985 and is currently 41 years old."
}
```

## Example 2 Parallel (Independence)
```json
{
    "tools": "get_weather(city): Check the weather.\nexchange_rate(currency_pair): exchange rate inquiry."
    "chain": ["get_weather, exchange_rate"]
}
```
**Output**:
```json
{
    "reasoning": "Chain is a flat list representing parallel tasks. The user inquired about both the weather and the exchange rate simultaneously, with no mutual influence between the two. Assuming the location is 'Beijing' and the exchange rate is 'USD to RMB'.",
    "task": "Please check the current weather in Beijing and also look up the exchange rate of US Dollar to RMB.",
    "answer": "Beijing is currently sunny with a temperature of 18 degrees. Additionally, the current exchange rate of the US dollar against the RMB is 1:7.09"
}
```
"""

