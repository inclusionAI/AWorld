# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import json
from typing import Dict, List, Any, Callable, Optional, Tuple

from aworld.agents.parallel_llm_agent import ParallelizableAgent
from aworld.agents.serial_llm_agent import SerialableAgent
from aworld.core.agent.agent_desc import agent_handoffs_desc
from aworld.core.agent.base import AgentFactory, BaseAgent
from aworld.core.common import ActionModel, Observation
from aworld.core.context.base import Context
from aworld.core.exceptions import AworldException
from aworld.logs.util import logger
from aworld.utils.common import new_instance, convert_to_subclass

WORKFLOW = "workflow"
HANDOFF = "handoff"


class Swarm(object):
    """Swarm is the multi-agent topology of AWorld, a collection of autonomous agents working together to
    solve complex problems through collaboration or competition.

    Swarm supports the key paradigms of workflow and handoff, and it satisfies the construction of various
    agent graphs, including DAG and DCG, such as star, tree, mesh, ring, and hybrid topology.
    """

    def __init__(self,
                 *args,  # agent
                 root_agent: BaseAgent = None,
                 max_steps: int = 0,
                 workflow: bool = True,
                 register_agents: List[BaseAgent] = [],
                 builder_cls: str = None,
                 event_driven: bool = True):
        self._communicate_agent = root_agent
        if root_agent and root_agent not in args:
            self.agent_list: List[BaseAgent] = [root_agent] + list(args)
        else:
            self.agent_list: List[BaseAgent] = list(args)

        self.setting_execute_type(workflow)
        self.max_steps = max_steps
        self._cur_step = 0
        self._event_driven = event_driven
        self.execute_type = WORKFLOW if self.workflow else HANDOFF
        if builder_cls:
            self.builder = new_instance(builder_cls, self)
        else:
            self.builder = WorkflowBuilder(self.agent_list, register_agents) if self.workflow \
                else HandoffBuilder(self.agent_list, register_agents)

        self.agent_graph: AgentGraph = None

        # global tools
        self.tools = []
        self.task = ''
        self.initialized: bool = False
        self._finished: bool = False

    def setting_execute_type(self, workflow):
        self.workflow = workflow
        all_single = True
        has_single = False
        has_pair = False
        for agent in self.agent_list:
            if isinstance(agent, (list, tuple)):
                has_pair = True

            if isinstance(agent, BaseAgent):
                has_single = True
            else:
                all_single = False

        if all_single:
            self.workflow = True
        else:
            # ((a1, a2), a3, (a4, a5))
            if has_single and has_pair:
                raise AworldException("The definition of swarm is confusing.")

        for agent in self.agent_list:
            if isinstance(agent, BaseAgent):
                agent = [agent]
            for a in agent:
                if a and a.event_driven:
                    self._event_driven = True
                    break

    def reset(self, content: Any, context: Context = None, tools: List[str] = []):
        """Resets the initial internal state, and init supported tools in agent in swarm.

        Args:
            tools: Tool names that all agents in the swarm can use.
        """
        # can use the tools in the agents in the swarm as a global
        if self.initialized:
            logger.warning(f"swarm {self} already init")
            return

        self.tools = tools
        # origin task
        self.task = content

        # build graph
        agent_graph: AgentGraph = self.builder.build()

        if not agent_graph.agents:
            logger.warning("No valid agent in swarm.")
            return

        agent_graph.topological_sequence()
        # Agent that communicate with the outside world, the default is the first if the root agent is None.
        if not self._communicate_agent:
            self._communicate_agent = agent_graph.ordered_agents[0]
        self.cur_agent = self.communicate_agent
        self.agent_graph = agent_graph

        if context is None:
            context = Context.instance()

        for agent in agent_graph.agents.values():
            agent.event_driven = self.event_driven
            if hasattr(agent, 'need_reset') and agent.need_reset:
                agent.context = context
                agent.reset({"task": content,
                             "tool_names": agent.tool_names,
                             "agent_names": agent.handoffs,
                             "mcp_servers": agent.mcp_servers})
            # global tools
            agent.tool_names.extend(self.tools)

        self.cur_step = 1
        self.initialized = True

    def find_agents_by_prefix(self, name, find_all=False):
        """Fild the agent list by the prefix name.

        Args:
            name: The agent prefix name.
            find_all: Find the total agents or the first match agent.
        """
        import re

        res = []
        for k, agent in self.agents.items():
            val = re.split(r"__uuid\w{6}uuid", k)[0]
            if name == val:
                res.append(agent)
                if not find_all:
                    return res
        return res

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
        agent: BaseAgent = self.agents.get(agent_name, None)
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
                if not res.content:
                    res.content = policy_info or ""

            return res
        else:
            logger.warning(f"{strategy} not supported now.")

    def supported_tools(self):
        """Tool names that can be used by all agents in Swarm."""
        self._check()
        return self.tools

    @property
    def has_cycle(self):
        self._check()
        return self.agent_graph.has_cycle()

    @property
    def agents(self):
        self._check()
        return self.agent_graph.agents

    @property
    def ordered_agents(self):
        self._check()
        return self.agent_graph.ordered_agents

    @property
    def communicate_agent(self):
        return self._communicate_agent

    @communicate_agent.setter
    def communicate_agent(self, agent: BaseAgent):
        self._communicate_agent = agent

    @property
    def event_driven(self):
        return self._event_driven

    @event_driven.setter
    def event_driven(self, event_driven):
        self._event_driven = event_driven

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


class EdgeInfo:
    clause: Optional[Callable[..., Any]] = None
    weight: Optional[float] = 0.


class AgentGraph:
    """The agent's graph is a directed graph, and can update the topology at runtime."""

    def __init__(self,
                 ordered_agents: List[BaseAgent] = [],
                 agents: Dict[str, BaseAgent] = {},
                 predecessor: Dict[str, Dict[str, EdgeInfo]] = {},
                 successor: Dict[str, Dict[str, EdgeInfo]] = {}):
        """Agent graph init.

        Args:
            ordered_agents: Agents ordered.
            agents: Agent nodes.
            predecessor: The direct predecessor of the agent.
            successor: The direct successor of the agent.
        """
        self.ordered_agents = ordered_agents
        self.agents = agents
        self.predecessor = predecessor
        self.successor = successor

    def topological_sequence(self) -> Tuple[List[BaseAgent], bool]:
        """Obtain the agent sequence of topology, and be able to determine whether the topology has cycle during the process.

        Returns:
            Topological sequence and whether it is a cycle topology, False represents DAG, True represents DCG.
        """
        in_degree = dict(filter(lambda k: k[1] > 0, self.in_degree().items()))
        zero_list = [v[0] for v in list(filter(lambda k: k[1] == 0, self.in_degree().items()))]

        res = []
        while zero_list:
            tmp = zero_list
            zero_list = []
            for agent_id in tmp:
                if agent_id not in self.agents:
                    raise RuntimeError("Agent topology changed during iteration")

                for key, _ in self.successor.get(agent_id).items():
                    try:
                        in_degree[key] -= 1
                    except KeyError as err:
                        raise RuntimeError("Agent topology changed during iteration")

                    if in_degree[key] == 0:
                        zero_list.append(key)
                        del in_degree[key]
            res.append(tmp)

        dcg = False
        if in_degree:
            logger.info("Agent topology contains cycle!")
            # sequence may be incomplete
            res.clear()
            dcg = True

        if not self.ordered_agents:
            for agent_ids in res:
                for agent_id in agent_ids:
                    self.ordered_agents.append(self.agents[agent_id])
        return res, dcg

    def has_cycle(self):
        res, is_dcg = self.topological_sequence()
        return is_dcg

    def add_node(self, agent: BaseAgent):
        if not agent:
            raise AworldException("agent is None, can not build the graph.")

        if agent.id() not in self.agents:
            self.agents[agent.id()] = agent
            self.successor[agent.id()] = {}
            self.predecessor[agent.id()] = {}
        else:
            logger.info(f"{agent.id()} already in agent graph.")

    def del_node(self, agent: BaseAgent):
        if not agent or agent.id() not in self.agents:
            return

        self.ordered_agents.remove(agent)
        del self.agents[agent.id()]

        successor = self.successor.get(agent.id(), {})
        for key, _ in successor.items():
            del self.predecessor[key][agent.id()]
        del self.successor[agent.id()]

        for key, _ in self.predecessor.get(agent.id(), {}):
            del self.successor[key][agent.id()]
        del self.predecessor[agent.id()]

    def add_edge(self, left_agent: BaseAgent, right_agent: BaseAgent):
        """Adding an edge between the left and the right agent means establishing the relationship
        between these two agents.

        Args:
            left_agent: As the agent node of the predecessor node.
            right_agent: As the agent node of the successor node.
        """
        if left_agent and left_agent.id() not in self.agents:
            raise RuntimeError(f"{left_agent.id()} not in agents node.")
        if right_agent and right_agent.id() not in self.agents:
            raise RuntimeError(f"{right_agent.id()} not in agents node.")

        if left_agent.id() not in self.successor:
            self.successor[left_agent.id()] = {}
            self.predecessor[left_agent.id()] = {}

        if right_agent.id() not in self.successor:
            self.successor[right_agent.id()] = {}
            self.predecessor[right_agent.id()] = {}

        self.successor[left_agent.id()][right_agent.id()] = EdgeInfo()
        self.predecessor[right_agent.id()][left_agent.id()] = EdgeInfo()

    def remove_edge(self, left_agent: BaseAgent, right_agent: BaseAgent):
        """Removing an edge between the left and the right agent means removing the relationship
        between these two agents.

        Args:
            left_agent: As the agent node of the predecessor node.
            right_agent: As the agent node of the successor node.
        """
        if left_agent.id() in self.successor and right_agent.id() in self.successor[left_agent.id()]:
            del self.successor[left_agent.id()][right_agent.id()]
        if right_agent.id() in self.predecessor and left_agent.id() in self.successor[right_agent.id()]:
            del self.predecessor[right_agent.id()][left_agent.id()]

    def in_degree(self) -> Dict[str, int]:
        """In degree of the agent is the number of agents pointing to the agent."""
        in_degree = {}
        for k, _ in self.agents.items():
            agents = self.predecessor[k]
            in_degree[k] = len(agents.values())
        return in_degree

    def out_degree(self) -> Dict[str, int]:
        """Out degree of the agent is the number of agents pointing out of the agent."""
        out_degree = {}
        for k, _ in self.agents.items():
            agents = self.successor[k]
            out_degree[k] = len(agents.values())
        return out_degree

    def loop_agent(self,
                   agent: BaseAgent,
                   max_run_times: int,
                   loop_point: str = None,
                   loop_point_finder: Callable[..., Any] = None,
                   stop_func: Callable[..., Any] = None):
        """Loop execution of the flow.

        Args:
            agent: The agent.
            max_run_times: Maximum number of loops.
            loop_point: Loop point of the desired execution.
            loop_point_finder: Strategy function for obtaining execution loop point.
            stop_func: Termination function.
        """
        from aworld.agents.loop_llm_agent import LoopableAgent

        if agent not in self.ordered_agents:
            raise RuntimeError(f"{agent.id()} not in swarm, agent instance {agent}.")

        loop_agent: LoopableAgent = convert_to_subclass(agent, LoopableAgent)
        # loop_agent: LoopableAgent = type(LoopableAgent)(agent)
        loop_agent.max_run_times = max_run_times
        loop_agent.loop_point = loop_point
        loop_agent.loop_point_finder = loop_point_finder
        loop_agent.stop_func = stop_func

        idx = self.ordered_agents.index(agent)
        self.ordered_agents[idx] = loop_agent

    def parallel_agent(self,
                       agent: BaseAgent,
                       agents: List[BaseAgent],
                       aggregate_func: Callable[..., Any] = None):
        """Parallel execution of agents.

        Args:
            agent: The agent.
            agents: Agents that require parallel execution.
            aggregate_func: Aggregate strategy function.
        """
        from aworld.agents.parallel_llm_agent import ParallelizableAgent

        if agent not in self.ordered_agents:
            raise RuntimeError(f"{agent.id()} not in swarm, agent instance {agent}.")
        for agent in agents:
            if agent not in self.ordered_agents:
                raise RuntimeError(f"{agent.id()} not in swarm, agent instance {agent}.")

        parallel_agent: ParallelizableAgent = convert_to_subclass(agent, ParallelizableAgent)
        parallel_agent.agents = agents
        parallel_agent.aggregate_func = aggregate_func

        idx = self.ordered_agents.index(agent)
        self.ordered_agents[idx] = parallel_agent

    def save(self, filepath: str):
        vals = {"agents": self.agents, "successor": self.successor, "predecessor": self.predecessor}
        json.dumps(vals)

    def load(self, filepath: str):
        pass


class TopologyBuilder:
    """Multi-agent topology base builder."""
    __metaclass__ = abc.ABCMeta

    def __init__(self, agent_list: List[BaseAgent], register_agents: List[BaseAgent] = []):
        self.agent_list = agent_list

        for agent in register_agents:
            TopologyBuilder.register_agent(agent)

    @abc.abstractmethod
    def build(self):
        """Build a multi-agent topology diagram using custom build strategies or syntax."""

    @staticmethod
    def register_agent(agent: BaseAgent):
        if agent.id() not in AgentFactory:
            AgentFactory._cls[agent.id()] = agent.__class__
            AgentFactory._desc[agent.id()] = agent.desc()
            AgentFactory._agent_conf[agent.id()] = agent.conf
            AgentFactory._agent_instance[agent.id()] = agent
        else:
            if agent.id() not in AgentFactory._agent_instance:
                AgentFactory._agent_instance[agent.id()] = agent
            if agent.desc():
                AgentFactory._desc[agent.id()] = agent.desc()


class WorkflowBuilder(TopologyBuilder):
    """Workflow mechanism, workflow is a deterministic process orchestration where each node must execute.

    There are three forms supported by the workflow builder: single agent, tuple of paired agents, and agent list.
    Examples:
    >>> agent1 = Agent(name='agent1'); agent2 = Agent(name='agent2'); agent3 = Agent(name='agent3')
    >>> agent4 = Agent(name='agent4'); agent5 = Agent(name='agent5'); agent6 = Agent(name='agent6')
    >>> Swarm(agent1, (agent2, agent3), [agent4, agent5], agent6)

    The meaning of the topology is that after agent1 completes execution, agent2 and agent3 are executed sequentially,
    then agent4 and agent5 are executed in parallel, and agent6 is executed after completion.
    """

    def build(self):
        """Built as workflow, different forms will be internally constructed as different agents,
        such as ParallelizableAgent, SerialableAgent or LoopableAgent.

        Returns:
            Direct topology diagram (AgentGraph) of the agents.
        """
        agent_graph = AgentGraph(agents={}, ordered_agents=[], predecessor={}, successor={})
        valid_agents = []
        for agent in self.agent_list:
            if isinstance(agent, BaseAgent):
                valid_agents.append(agent)
            elif isinstance(agent, tuple):
                serial_agent = SerialableAgent(name="_".join(agent), conf=agent[0].conf, agents=list(agent))
                valid_agents.append(serial_agent)
            elif isinstance(agent, list):
                # list
                parallel_agent = ParallelizableAgent(name="_".join(agent), conf=agent[0].conf, agents=agent)
                valid_agents.append(parallel_agent)
            else:
                raise RuntimeError(f"agent in {agent} is not a agent or agent list, please check it.")

        if not valid_agents:
            raise RuntimeError(f"no valid agent in swarm to build execution graph.")

        last_agent = None
        for agent in valid_agents:
            TopologyBuilder.register_agent(agent)

            agent_graph.add_node(agent)
            if last_agent:
                agent_graph.add_edge(last_agent, agent)
            last_agent = agent
        return agent_graph


class HandoffBuilder(TopologyBuilder):
    """Handoff mechanism using agents as tools, but during the runtime,
    the agent remains an independent entity with a state.

    Handoffs builder only supports tuple of paired agents forms.
    Examples:
    >>> agent1 = Agent(name='agent1'); agent2 = Agent(name='agent2'); agent3 = Agent(name='agent3')
    >>> agent4 = Agent(name='agent4'); agent5 = Agent(name='agent5'); agent6 = Agent(name='agent6')
    >>> Swarm((agent1, agent2), (agent1, agent3), (agent2, agent3), determinacy=False)
    """

    def build(self):
        """Build a graph in pairs, with the right agent serving as the tool on the left.

        Using pure AI to drive the flow of the entire topology diagram, one agent's decision
        hands off control to another. Agents may be fully connected or circular, depending
        on the defined pairs of agents.

        Returns:
            Direct topology diagram (AgentGraph) of the agents.
        """
        valid_agent_pair = []
        for pair in self.agent_list:
            if not isinstance(pair, (list, tuple)):
                raise RuntimeError(f"{pair} is not a tuple or list value, please check it.")
            elif len(pair) != 2:
                raise RuntimeError(f"{pair} is not a pair, please check it.")

            valid_agent_pair.append(pair)

        if not valid_agent_pair:
            raise RuntimeError(f"no valid agent pair to build execution graph.")

        # agent handoffs graph build.
        agent_graph = AgentGraph()
        for pair in valid_agent_pair:
            TopologyBuilder.register_agent(pair[0])
            TopologyBuilder.register_agent(pair[1])

            agent_graph.add_node(pair[0])
            agent_graph.add_node(pair[1])
            agent_graph.add_edge(pair[0], pair[1])

            # explicitly set handoffs in the agent
            pair[0].handoffs.append(pair[1].id())
        return agent_graph
