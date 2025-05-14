
tool_call_template = """


**call {function_name}**

```tool_call_arguments
{function_arguments}
```
        
```tool_call_result
{function_result}
```


"""

step_loading_template = """
```loading
{data}
```
"""