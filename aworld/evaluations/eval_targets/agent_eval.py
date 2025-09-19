from aworld.evaluations.base import EvalTarget, EvalDataCase
from aworld.agents.llm_agent import Agent
from aworld.runner import Runners


class AgentEvalTarget(EvalTarget[dict]):

    def __init__(self, agent: Agent, query_column: str = 'query'):
        self.agent = agent
        self.query_column = query_column

    async def predict(self, input: EvalDataCase[dict]) -> dict:
        response = await Runners.run(input.case_data[self.query_column], agent=self.agent)
        return {"answer": response.answer}
