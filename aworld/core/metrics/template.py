from email.policy import default
from typing import Sequence
from pydantic import BaseModel, Field, root_validator
from typing import Optional


class MetricTemplate(BaseModel):
    """
    MetricTemplate is a class for defining a metric template.
    """
    # 定义一个枚举类型 
    type: str
    name: str
    description: Optional[str] = None
    unit: Optional[str] = Field(default="1")
    labels: Optional[list[str]] = None
    buckets: Optional[Sequence[float]] = None

    @root_validator(pre=True)
    def set_default_description(cls, values):
        """
        Set the default description if it is not set.
        """
        if 'description' not in values or values['description'] is None:
            values['description'] = values['name']
        return values

class MetricTemplates:
    
    REQUEST_COUNT = MetricTemplate(**{
        "type": "counter",
        "name": "request_count",
        "description": "The number of requests received",
        "unit": "1",
        "labels": ["method", "status"]
    })
    
    REQUEST_LATENCY = MetricTemplate(**{
        "type": "histogram",
        "name": "request_latency",
        "description": "The latency of requests",
        "unit": "ms",
        "labels": ["method", "status"],
        # "buckets": [0.01, 0.05, 0.1, 0.5, 1, 5, 10, 50, 100, 500, 1000]
    })