tool_card_template = """
\n\n
```tool_card
{tool_card_content}
```
\n\n
"""

tool_call_template = """


**call {tool_name}#{function_name}**[{tool_type}]

```tool_call_arguments
{function_arguments}
```

```tool_call_result
{function_result}
```

{images}


"""

step_loading_template = """
```loading
{data}
```
"""