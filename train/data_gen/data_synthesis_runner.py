# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import json
import os
from copy import deepcopy
from typing import Tuple

from jsonlines import jsonlines

from aworld.config import AgentConfig, wipe_secret_info
from aworld.core.agent.swarm import Swarm
from aworld.core.task import Runner, Task, TaskResponse
from aworld.logs.util import logger
from aworld.runner import Runners
from aworld.runners.runtime_engine import RuntimeEngine
from aworld.runners.utils import runtime_engine
from aworld.utils.common import new_instance
from train.data_gen.agents.task_gen import TaskGeneratorAgent
from train.data_gen.agents.tool_gen import ToolGeneratorAgent
from train.data_gen.agents.tool_orchestra import ToolOrchestratorAgent
from train.data_gen.agents.tool_select import ToolSelectAgent
from train.data_gen.capability_ontology import CapabilityOntology
from train.data_gen.ontology_operator import OntologyOperator
from train.data_gen.schema import Specification, GeneratedTool, ToolSynthesisConfig, TreeNode, GenerationStrategy, \
    OntologyConfig, DataSynthesisConfig, TaskSynthesisConfig
from train.data_gen.tool_repository import ToolRepository


class DataSynthesisRunner(Runner):
    def __init__(self, task, conf: DataSynthesisConfig, **kwargs):
        self.task = task
        self.conf = conf
        self.tool_repository = None
        self.tool_gen_event = asyncio.Event()

    async def do_run(self):
        """The workflow of data synthesis, tool_gen -> task_gen -> task_exec -> data_eval.

        Returns:
            Path of the dataset used for training and testing, tool data.
        """
        dir_name = self.conf.dir_name or "spec"
        os.makedirs(dir_name, exist_ok=True)
        self.dir_name = dir_name
        tool_data_path = f"{dir_name}/{self.conf.tool_file_name}" if self.conf.tool_file_name else None

        ## Tool generate
        if self.conf.gen_tools:
            tool_data_path = await self.tool_synthesis(tool_data_path=tool_data_path)

            if self.conf.eval_data:
                # eval tools
                pass
        else:
            self.tool_gen_event.set()

        # Waiting for pre-processing tool synthesis
        await self.tool_gen_event.wait()

        ## Task Generate
        train_data_path, test_data_path = None, None
        if self.conf.gen_tasks:
            train_data_path, test_data_path = await self.sample_synthesis(dir_name=dir_name,
                                                                          tool_data_path=tool_data_path)

        ## Task Execute
        if train_data_path and self.conf.exec_tasks:
            pass

        # Result data evaluate
        if test_data_path and self.conf.eval_data:
            # eval synthesis samples
            pass

        logger.info(f"{self.conf.name} data synthesis done. "
                    f"Config: {wipe_secret_info(self.conf.model_dump(), ['llm_api_key'])}")
        return train_data_path, test_data_path, tool_data_path

    async def tool_synthesis(self, tool_data_path: str = None) -> str:
        """Synthesis tools.

        Args:
            tool_data_path: Path of reading or writing tools.

        Returns:
            The path of the file containing the tool list.
        """
        # Check if a tool needs to be generated
        tool_data_path = tool_data_path or f"{self.dir_name}/tools.jsonl"
        if not os.path.exists(tool_data_path):
            # tool needs to be generated
            if not self.conf.tool_gen_config:
                llm_config = self.conf.llm_config
                tool_gen_config = ToolSynthesisConfig(llm_config=llm_config,
                                                      ontology_config=OntologyConfig(
                                                          llm_config=llm_config,
                                                          task=self.task
                                                      ))
            else:
                tool_gen_config = self.conf.tool_gen_config
            await self._do_gen_tools(tool_gen_config)

            # Save tools
            if not self.tool_repository:
                self.tool_repository = ToolRepository()
            await self.tool_repository.save_to_json(tool_data_path)
        else:
            logger.info(f"{tool_data_path} exists, will use it and skip tool generation.")

        if not self.tool_gen_event.is_set():
            self.tool_gen_event.set()
        return tool_data_path

    async def sample_synthesis(self, dir_name: str = None, tool_data_path: str = None) -> Tuple[str, str]:
        """Synthesis samples.

        Args:
            dir_name: Directory of config or other spec content.
            tool_data_path: The path of the file containing the tool list.

        Returns:
            The path of the file containing the sample list.
        """
        output_dir = f"./{dir_name}/data_gen"
        os.makedirs(output_dir, exist_ok=True)

        # tool repository
        if tool_data_path:
            await self._do_read_tools(tool_data_path)

        if not self.conf.task_gen_config:
            task_gen_config = TaskSynthesisConfig(llm_config=self.conf.llm_config)
        else:
            task_gen_config = self.conf.task_gen_config
        # Number of generated samples
        sample_count = task_gen_config.gen_number

        tasks = []
        for i in range(sample_count):
            # TODO: load yaml create agents and swarm
            if task_gen_config.use_tool:
                tool_select = ToolSelectAgent(tool_repository=self.tool_repository,
                                              conf=AgentConfig(llm_config=self.conf.llm_config))
                tool_orche = ToolOrchestratorAgent(tool_repository=self.tool_repository,
                                                   conf=AgentConfig(llm_config=self.conf.llm_config))
                # QA is default
                task_gen = TaskGeneratorAgent(tool_repository=self.tool_repository,
                                              conf=AgentConfig(llm_config=self.conf.llm_config))
                # based tools to generate
                # swarm = load_swarm_from_dict(configs)[0]
                swarm = Swarm(tool_select, tool_orche, task_gen)
                task = Task(input=tool_data_path, swarm=swarm)
            else:
                # QA is default
                llm_config = deepcopy(self.conf.llm_config)
                llm_config.llm_temperature = 1.
                task_gen = TaskGeneratorAgent(tool_repository=None,
                                              conf=AgentConfig(llm_config=llm_config))
                swarm = Swarm(task_gen)
                task = Task(input=self.task, swarm=swarm)

            tasks.append(task)
        results = await Runners.run_task(tasks)

        # Write results to file as a dataset
        datasets = []
        for _, result in results.items():
            answer = result.answer.replace("```json", "").replace("```", "")
            try:
                samples = json.loads(answer)
                for sample in samples:
                    datasets.append(sample)
            except Exception as e:
                logger.error(f"{answer} parse fail.")

        # can be improved!!!
        split_idx = int(len(datasets) * 0.9)
        train_datasets, test_datasets = datasets[:split_idx], datasets[split_idx:]
        train_dataset_path = os.path.join(output_dir, f"train_{self.conf.task_file_postfix}")
        test_dataset_path = os.path.join(output_dir, f"test_{self.conf.task_file_postfix}")
        with jsonlines.open(train_dataset_path, "w") as f:
            f.write_all(train_datasets)
        with jsonlines.open(test_dataset_path, "w") as f:
            f.write_all(test_datasets)
        return train_dataset_path, test_dataset_path

    async def _do_read_tools(self, tool_data_path: str):
        tools = []
        with open(tool_data_path, "r+", encoding="utf8") as f:
            for item in jsonlines.Reader(f):
                spec_dict = item.pop("spec")
                spec = Specification(**spec_dict)

                item["spec"] = spec
                tool = GeneratedTool(**item)
                tools.append(tool)
                if not self.tool_repository:
                    self.tool_repository = ToolRepository()
                await self.tool_repository.add_tool(tool)
        return tools

    async def _do_gen_tools(self, tool_gen_config: ToolSynthesisConfig):
        tool_ontology = CapabilityOntology(config=tool_gen_config.ontology_config)
        await tool_ontology.build()

        tool_synth_op = OntologyOperator(ontology=tool_ontology)
        engine: RuntimeEngine = await runtime_engine(self.conf.run_conf)
        gen_number = tool_gen_config.gen_number
        batch_size = tool_gen_config.batch_size
        batch = gen_number // batch_size
        times = batch if gen_number % batch_size == 0 else batch + 1
        generated_num = 0
        results = []
        for k in range(times):
            # create tools based cate
            cates = tool_ontology.cate_nodes.values()
            tree_nodes = []
            for cate in cates:
                capabilities = list(cate.children.keys())
                for ability in capabilities:
                    spec = await tool_synth_op.single_capability(cate.name, ability)
                    tree_nodes.append(spec.tree_node)

            batch_tools = []
            for idx, tree_node in enumerate(tree_nodes):
                task_resp = await engine.execute([self._exec_tool_gen_agent],
                                                 tree_node, tool_gen_config, tool_synth_op)
                answer = list(task_resp.values())[0]
                tools = answer.answer
                if tools:
                    batch_tools.extend(tools)

            # Deduplication

            # Inspection Quantity
            generated_num += len(batch_tools)

            results.extend(batch_tools)
            # write to store
            if not self.tool_repository:
                self.tool_repository = ToolRepository()
            await self.tool_repository.add_tools(results)

            # With a certain number of tools, can start synthesizing
            # if not self.tool_gen_event.is_set() and generated_num >= batch_size:
            #     self.tool_gen_event.set()

        return results

    async def _exec_tool_gen_agent(self,
                                   tree_node: TreeNode,
                                   tool_gen_config: ToolSynthesisConfig,
                                   tool_synth_op: OntologyOperator) -> TaskResponse:
        if tool_gen_config.strategy == GenerationStrategy.LLM or tool_gen_config.strategy == GenerationStrategy.MODEL:
            # special agent
            tool_gen_agent = ToolGeneratorAgent(tool_synth_op=tool_synth_op,
                                                conf=AgentConfig(llm_config=tool_gen_config.llm_config))
        else:
            tool_gen_agent = new_instance(tool_gen_config.rule_cls, tool_synth_op=tool_synth_op)
        tool_gen_agent.reset()

        # use synthesis op
        # api_spec: Specification = await tool_synth_op.sample(category=tool_gen_config.category,
        #                                                      complexity=tool_gen_config.complexity)
        # tree_node = api_spec.tree_node

        task = Task(input=tree_node, agent=tool_gen_agent)
        res = await Runners.run_task(task=task)
        return res.get(task.id)
