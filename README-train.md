# AWorld Framework for GAIA Question Processing and Agentic Training

## 1. Cluster Deployment
Please refer to `aworlddistributed/README.md` for detailed information.
aworlddistributed provides Docker image building capabilities that can be deployed to Kubernetes clusters. The deployment process includes:

- Docker image building commands and configurations
- Kubernetes deployment manifests for container orchestration

## 2. Training Sample Collection

The system collects training samples through processing GAIA questions and recording agent trajectories. This process includes:

### Client Requests
Clients send HTTP API requests to the K8s cluster to solve GAIA questions. The system supports concurrent request processing:

Python Client Usage:

```python
# Initialize AworldTaskClient with server endpoints
AWORLD_TASK_CLIENT = AworldTaskClient(
    know_hosts=["localhost:9299", "localhost:9399", "localhost:9499"] # For k8s cluster services, only the cluster address needs to be set
)

async def _run_gaia_task(gaia_question_id: str) -> None:
    """Run a single Gaia task with the given question ID.
    
    Args:
        gaia_question_id: The ID of the question to process
    """
    global AWORLD_TASK_CLIENT
    task_id = str(uuid.uuid4())
    
    # Submit task to Aworld server
    await AWORLD_TASK_CLIENT.submit_task(
        AworldTask(
            task_id=task_id,
            agent_id="gaia_agent",
            agent_input=gaia_question_id,
            session_id="session_id",
            user_id="SYSTEM"
        )
    )
    
    # Get and print task result
    task_result = await AWORLD_TASK_CLIENT.get_task_state(task_id=task_id)
    print(task_result)

async def _batch_run_gaia_task(start_i: int, end_i: int) -> None:
    """Run multiple Gaia tasks in parallel.
    
    Args:
        start_i: Starting question ID
        end_i: Ending question ID
    """
    tasks = [
        _run_gaia_task(str(i))
        for i in range(start_i, end_i + 1)
    ]
    await asyncio.gather(*tasks)

if __name__ == '__main__':
    # Run batch processing for questions 1-5
    asyncio.run(_batch_run_gaia_task(1, 5))
```

### Cluster Processing
- K8s cluster receives and processes requests
- Multiple pods handle workload in parallel
- Each pod executes necessary tasks to solve GAIA questions

### Trajectory Recording
- Key trajectory information recorded during task execution
- Complete interaction process captured

Example of Trajectory (showing only key fields):

```json
[
  {
    "exp_meta": {
      "task_id": "0a602930-ac18-4e5d-8a7b-3799170f24aa",
      "agent_id": "GaiaAgent",
      "step": 2,
      "pre_agent": "GaiaAgent"
    },
    "exp_data": {
      "state": {
        "from_agent_name": "GaiaAgent",
        "content": "{\"query\":\"CarlNebelWikipedia\",\"results\":[{\"id\":\"google-0\",\"title\":\"CarlNebel-Wikipedia\",\"url\":\"https://en.wikipedia.org/wiki/Carl_Nebel\",\"snippet\":\"CarlNebel...CarlNebel(18March1805–4June1855)wasaGermanengineer,architectanddraughtsman,bestknownforhisdetailedpaintingsandlithographic...\",\"source\":\"google\"}],\"count\":10,\"source\":\"google\",}",
        "image": "",
        "action_result": [
          {
            "is_done": false,
            "content": "{\"query\":\"CarlNebelWikipedia\",\"results\":[{\"id\":\"google-0\",\"title\":\"CarlNebel-Wikipedia\",\"url\":\"https://en.wikipedia.org/wiki/Carl_Nebel\",\"snippet\":\"CarlNebel...CarlNebel(18March1805–4June1855)wasaGermanengineer,architectanddraughtsman,bestknownforhisdetailedpaintingsandlithographic...\",\"source\":\"google\"}],\"count\":10,\"source\":\"google\",}"
          }
        ],
        "images": [],
        "info": {}
      },
      "actions": [
        {
          "tool_name": "mcp",
          "agent_name": null,
          "action_name": "ms-playwright__browser_navigate",
          "params": {
            "url": "https://en.wikipedia.org/wiki/Carl_Nebel"
          },
          "policy_info": ""
        }
      ],
      "reward_t": 0,
      "adv_t": 0.0,
      "v_t": 0.0,
      "messages": [
        "{'role': 'system', 'content': 'You are an all-capable AI assistant...'}",
        "{'role': 'user', 'content': \"What is the latest chronological year date...\", 'tool_call_id': None}",
        "{'role': 'assistant', 'content': None, 'tool_calls': [{\"id\": \"tooluse_4M4IUsftR4W2KxYxc7G26A\", \"type\": \"function\", \"function\": {\"name\": \"mcp__search_server__mcpsearchgoogle\", \"arguments\": \"{\\\"query\\\": \\\"Carl Nebel Wikipedia\\\"}\"}}]}",
        "{'role': 'tool', 'content': '{\"query\":\"Carl Nebel Wikipedia\",\"results\":...}'}"
      ]
    },
    "id": "0a602930-ac18-4e5d-8a7b-3799170f24aa_2_GaiaAgent_0"
  }
]
```

## 3. Agentic Training

- Trajectory Data Processing
- Agentic Model Training
- Training Metrics (e.g., Loss Curves, GAIA test accuracy)

The system provides end-to-end support from high-concurrency question processing to agentic model training, with comprehensive monitoring and optimization capabilities.