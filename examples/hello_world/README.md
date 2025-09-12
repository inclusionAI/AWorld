# Hello world of AWorld

Provided examples in `hello_world` dir of agent as tool and agent use tool, and
provides `Swarm` concept to support the topology of multi-agent interaction.

As the **first** agent framework that seamlessly supports running on different distributed computing engines,
while possessing the vast majority of capabilities required by agents,
please enjoy the abilities of **AWorld**.

## Run hello world

**NOTE**:

- Python 3.11 or higher
- Set `LLM_MODEL_NAME`, `LLM_BASE_URL`, `LLM_API_KEY` etc. environment variables.

```python
import os

os.environ["LLM_MODEL_NAME"] = "gpt-4o"
os.environ["LLM_BASE_URL"] = "your url"
os.environ["LLM_API_KEY"] = "your key"
```

- Run any one of `run*.py` script, and will get the answer 'hello world'.

```shell
python run.py
python run_on_ray.py
python run_on_spark.py
```

**NOTE**: PySpark version needs to be compatible with JDK version. 