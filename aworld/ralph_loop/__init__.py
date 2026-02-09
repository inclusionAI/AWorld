# coding: utf-8
# Copyright (c) inclusionAI.
"""
RALPH Pattern Implementation in AWorld.

And RALPH has been redefined in AWorld:
- R (Run): Execute tasks with strategic planning
- A (Analyze): Validate outputs against multiple criteria
- L (Learn): Reflect on execution and extract insights
- P (Plan): Replan based on feedback and learnings
- H (Halt): Detect termination conditions

Basic components reused (Context, Event, Runtime Engine, Storage, etc.)

Main Components:
- RalphRunner: Main loop controller
- Analyzer: User mission process and analysis
- Strategic Planning: LLM-driven task decomposition
- Loop Context: Global Context engineering
- Validation: Multi-dimensional output verification
- Reflection: Quality analysis, insight
- Stop Detector: Stop condition detect
- State Management: tasks and state tracking
"""

import os

from aworld.evaluations import _auto_discover_scorers

# Auto-discover validation scorers
_auto_discover_scorers(
    current_dir=os.path.join(os.path.dirname(__file__), 'validate'),
    package_name=f'{__name__}.validate'
)
