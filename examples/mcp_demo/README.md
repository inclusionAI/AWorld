MCP demo

1. Start example mcp server use the command: 
```shell
simple_server.py
```

2. Configure the `llm_api_key` and `llm_base_url`, or relevant parameters. 
3. Run the pipeline: 
```shell
    python run.py
```

4. View the logs in the console, and the following key information indicates successful execution:
```text
mcp observation: container_id=None observer=None ability=None from_agent_name=None to_agent_name=None content='{"result": 25000.0}' dom_tree=None image=None action_result=[ActionResult(is_done=False, success=False, content='{"result": 25000.0}', error=None, keep=True, action_name='divide', tool_name='simple-calculator', tool_id='call_Sb4c16wDzUvdTPaqqqdNH6Er', metadata={})] images=[] info={} 
``` 