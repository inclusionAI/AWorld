# AWorld Framework for GAIA Question Processing and Agentic Training

## 1. Cluster Deployment

### 1.1 Build Docker Image
First, build the Docker image locally:
```bash
# Build the Docker image
docker build -t aworld:latest .
```

### 1.2 Create Kubernetes Cluster
Create a new cluster on your cloud platform (e.g., AWS EKS, GCP GKE, or Azure AKS).

### 1.3 Deploy Services
Deploy the services using the built image to your Kubernetes cluster:
```yaml
# Example deployment manifest
apiVersion: apps/v1
kind: Deployment
metadata:
  name: aworld-service
spec:
  replicas: 3
  template:
    spec:
      containers:
      - name: aworld
        image: aworld:latest
        ports:
        - containerPort: 9299
```

## 2. Training Sample Collection

### 2.1 Client Request Processing
Clients send HTTP API requests to the K8s cluster to solve user queries. The system supports concurrent request processing:

```python
# Initialize AworldTaskClient with server endpoints
AWORLD_TASK_CLIENT = AworldTaskClient(
    know_hosts=["localhost:9299", "localhost:9399", "localhost:9499"] # For k8s cluster services, only the cluster address needs to be set
)

async def _run_gaia_task(gaia_question_id: str) -> None:
    """Run a single Gaia task with the given question ID."""
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
```

### 2.2 Service-Side Processing Flow
When a request is received, the following process occurs in the service:

1. **Request Distribution**
   - K8s cluster receives the incoming request
   - Request is distributed to available pods
   - Multiple pods handle workload in parallel

2. **Task Execution**
   - Each pod processes the assigned task
   - Agent performs necessary actions to solve the query
   - System maintains state and context throughout the process

3. **Trajectory Recording**
   The system automatically records the complete interaction process, including:
   - Task metadata (ID, agent info, step count)
   - State transitions
   - Actions taken
   - Messages exchanged

Example of recorded trajectory:
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
        "content": "...",
        "action_result": [...]
      },
      "actions": [...],
      "messages": [...]
    }
  }
]
```

## 3. Agentic Training

The training process includes:
- Trajectory Data Processing
- Agentic Model Training
- Training Metrics (e.g., Loss Curves, GAIA test accuracy)

The system provides end-to-end support from high-concurrency question processing to agentic model training, with comprehensive monitoring and optimization capabilities.