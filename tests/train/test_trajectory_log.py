import unittest
import json
from typing import Dict, List
from aworld.core.context.base import AgentTokenIdStep, AgentTokenIdTrajectory
from aworld.utils.serialized_util import to_serializable


class SingleStepTest(unittest.IsolatedAsyncioTestCase):

    def test_to_json(self):
        step = AgentTokenIdStep(
            step=1,
            tool_call_ids=["call_1"],
            prompt_token_ids=[1, 2, 3],
            tool_resp_token_ids=[4, 5, 6],
            finish_reason="stop"
        )
        print(json.dumps(step.to_dict()))

        trajectory = AgentTokenIdTrajectory(
            agent_id="agent_1",
            token_id_steps=[step]
        )
        print(json.dumps(trajectory.to_dict()))

        agent_token_id_traj: Dict[str, List[AgentTokenIdTrajectory]] = {}
        agent_token_id_traj["agent_1"] = [trajectory]
        print(json.dumps(to_serializable(agent_token_id_traj)))
