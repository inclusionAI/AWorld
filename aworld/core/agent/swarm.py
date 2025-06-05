# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Dict, List

from aworld.core.agent.agent_desc import agent_handoffs_desc
from aworld.core.agent.base import AgentFactory
from aworld.core.agent.llm_agent import Agent
from aworld.core.common import ActionModel, Observation
from aworld.core.context.base import Context
from aworld.logs.util import logger

SEQUENCE = "sequence"
SOCIAL = "social"
SEQUENCE_EVENT = "sequence_event"
SOCIAL_EVENT = "social_event"


class Swarm(object):
    """Multi-agent topology.

    Examples:
        >>> agent1 = Agent(name='agent1'); agent2 = Agent(name='agent2'); agent3 = Agent(name='agent3')
        # sequencial
        >>> Swarm(agent1, agent2, agent3)
        # social
        >>> Swarm((agent1, agent2), (agent1, agent3), (agent2, agent3), sequence=False)
    """

    def __init__(self, *args, root_agent: Agent = None, sequence: bool = True, max_steps: int = 0, **kwargs):
        self.communicate_agent = root_agent
        if root_agent and root_agent not in args:
            self._topology = [root_agent] + list(args)
        else:
            self._topology = args
        self._ext_params = kwargs
        self.sequence = sequence
        self.max_steps = max_steps
        self.initialized = False
        self._finished = False
        self._cur_step = 0
        self._event_driven = kwargs.get('event_driven', False)
        for agent in self._topology:
            if isinstance(agent, Agent):
                agent = [agent]
            for a in agent:
                if a and a.event_driven:
                    self._event_driven = True
                    break
            if self._event_driven:
                break

    @property
    def event_driven(self):
        return self._event_driven

    @event_driven.setter
    def event_driven(self, event_driven):
        self._event_driven = event_driven

    def _init(self, **kwargs):
        """Swarm init, build the agent or agent pairs to the topology of agents."""
        # prebuild
        valid_agent_pair = []
        for pair in self._topology:
            if isinstance(pair, (list, tuple)):
                self.topology_type = SOCIAL
                # (agent1, agent2)
                if len(pair) != 2:
                    raise RuntimeError(f"{pair} is not a pair value, please check it.")
                elif not isinstance(pair[0], Agent) or not isinstance(pair[1], Agent):
                    raise RuntimeError(f"agent in {pair} is not a base agent instance, please check it.")
                valid_agent_pair.append(pair)
            else:
                # agent
                if not isinstance(pair, Agent):
                    raise RuntimeError(f"agent in {pair} is not a base agent instance, please check it.")
                self.topology_type = SEQUENCE
                valid_agent_pair.append((pair,))

        if not valid_agent_pair:
            logger.warning("no valid agent pair to build graph.")
            return

        # Agent that communicate with the outside world, the default is the first if the root agent is None.
        if self.communicate_agent is None:
            self.communicate_agent: Agent = valid_agent_pair[0][0]
        # agents in swarm.
        self.agents: Dict[str, Agent] = dict()
        self.ordered_agents: List[Agent] = []

        # agent handoffs build.
        for pair in valid_agent_pair:
            if self.sequence or self.topology_type == SEQUENCE:
                self.ordered_agents.append(pair[0])
                if len(pair) == 2:
                    self.ordered_agents.append(pair[1])

            if pair[0] not in self.agents:
                self.agents[pair[0].name()] = pair[0]
            if pair[0].name() not in AgentFactory:
                AgentFactory._cls[pair[0].name()] = pair[0].__class__
                AgentFactory._desc[pair[0].name()] = pair[0].desc()
                AgentFactory._agent_conf[pair[0].name()] = pair[0].conf
                AgentFactory._agent_instance[pair[0].name()] = pair[0]
            elif pair[0].desc():
                AgentFactory._desc[pair[0].name()] = pair[0].desc()

            if len(pair) == 1:
                continue

            if pair[1] not in self.agents:
                self.agents[pair[1].name()] = pair[1]
                if pair[1].name() not in AgentFactory:
                    AgentFactory._cls[pair[1].name()] = pair[1].__class__
                    AgentFactory._desc[pair[1].name()] = pair[1].desc()
                    AgentFactory._agent_conf[pair[1].name()] = pair[1].conf
                    AgentFactory._agent_instance[pair[1].name()] = pair[1]
                elif pair[1].desc():
                    AgentFactory._desc[pair[1].name()] = pair[1].desc()

            if self.topology_type == SOCIAL:
                # need to explicitly set handoffs in the agent
                pair[0].handoffs.append(pair[1].name())

        if self.sequence:
            self.topology_type = SEQUENCE

        self._cur_step = 1
        # event driven
        if self.event_driven:
            for _, agent in self.agents.items():
                agent.event_driven = True
            if self.topology_type == SEQUENCE:
                self.topology_type = SEQUENCE_EVENT
            elif self.topology_type == SOCIAL:
                self.topology_type = SOCIAL_EVENT

    def reset(self, content: str, context: Context = None, tools: List[str] = []):
        """Resets the initial internal state, and init supported tools in agent in swarm.

        Args:
            tools: Tool names that all agents in the swarm can use.
        """
        # can use the tools in the agents in the swarm as a global
        self.tools = tools

        self._init()
        if not self.agents:
            logger.warning("No valid agent in swarm.")
            return

        self.cur_agent = self.communicate_agent

        if context is None:
            context = Context.instance()

        for agent in self.agents.values():
            if agent.need_reset:
                agent.context = context
                agent.reset({"task": content,
                             "tool_names": agent.tool_names,
                             "agent_names": agent.handoffs,
                             "mcp_servers": agent.mcp_servers})
            # global tools
            agent.tool_names.extend(self.tools)
        self.initialized = True

    def _check(self):
        if not self.initialized:
            self.reset('')

    def handoffs_desc(self, agent_name: str = None, use_all: bool = False):
        """Get agent description by name for handoffs.

        Args:
            agent_name: Agent unique name.
        Returns:
            Description of agent dict.
        """
        self._check()
        agent: Agent = self.agents.get(agent_name, None)
        return agent_handoffs_desc(agent, use_all)

    def action_to_observation(self, policy: List[ActionModel], observation: List[Observation], strategy: str = None):
        """Based on the strategy, transform the agent's policy into an observation, the case of the agent as a tool.

        Args:
            policy: Agent policy based some messages.
            observation: History of the current observable state in the environment.
            strategy: Transform strategy, default is None. enum?
        """
        self._check()

        if not policy:
            logger.warning("no agent policy, will return origin observation.")
            # get the latest one
            if not observation:
                raise RuntimeError("no observation and policy to transform in swarm, please check your params.")
            return observation[-1]

        if not strategy:
            # default use the first policy
            policy_info = policy[0].policy_info

            if not observation:
                res = Observation(content=policy_info)
            else:
                res = observation[-1]
                if res.content is None:
                    res.content = ''

                if policy_info:
                    res.content += policy_info
            return res
        else:
            logger.warning(f"{strategy} not supported now.")

    def supported_tools(self):
        """Tool names that can be used by all agents in Swarm."""
        self._check()
        return self.tools

    @property
    def cur_step(self) -> int:
        return self._cur_step

    @cur_step.setter
    def cur_step(self, step):
        self._cur_step = step

    @property
    def finished(self) -> bool:
        """Need all agents in a finished state."""
        self._check()
        if not self._finished:
            self._finished = all([agent.finished for _, agent in self.agents.items()])
        return self._finished

    @finished.setter
    def finished(self, finished):
        self._finished = finished
