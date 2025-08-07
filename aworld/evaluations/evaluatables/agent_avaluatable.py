from aworld.evaluations.base import Evaluatable
from aworld.agents.llm_agent import Agent
from aworld.runner import Runners


class AgentEvaluatable(Evaluatable):

    def __init__(self, agent: Agent, query_column: str = 'query'):
        self.agent = agent
        self.query_column = query_column

    async def predict(self, input: dict) -> dict:
        response = await Runners.run(input[self.query_column], agent=self.agent)
        return {"answer": response.answer}
