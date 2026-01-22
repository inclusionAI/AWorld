# coding: utf-8
# Copyright (c) inclusionAI.
import asyncio
import json
import os
from typing import Any, List, Tuple, Dict, Union
import pandas as pd
import yaml

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig, load_config, ModelConfig
from aworld.config.agent_loader import load_agents_from_dict
from aworld.core.agent.swarm import Swarm
from aworld.core.context.base import Context
from aworld.core.task import Runner, TaskResponse, Task
from aworld.logs.util import logger
from aworld.output import StreamingOutputs
from aworld.output.base import Output
from aworld.output.outputs import DefaultOutputs, Outputs
from aworld.runner import Runners
from aworld.runners.runtime_engine import RuntimeEngine
from aworld.runners.utils import runtime_engine
from aworld.tools.human.human import HUMAN
from aworld.utils import import_package, import_packages
from aworld.utils.run_util import exec_tool
from train.evolve.util import evolution_plan_render

from train.integration.verl.reward_func import verl_default_reward_func
from train.data_gen.data_synthesis_runner import DataSynthesisRunner
from train.data_gen.schema import DataSynthesisConfig
from train.evolve.evolve_pipeline_agent import EvolutionPipelineAgent
from train.evolve.config import EvolutionConfig
from train.trainer.agent_trainer import AgentTrainer, TRAIN_PROCESSOR
from train.trainer.utils import TRAIN_DEFAULT_CONFIG


class EvolutionRunner(Runner):
    def __init__(self, task: Any, config: EvolutionConfig):
        self.task = task
        if hasattr(task, "outputs") and isinstance(task.outputs, Outputs):
            self.outputs = task.outputs
        else:
            self.outputs = StreamingOutputs()
        self.conf = config
        self.tool_repository = None
        self.event = asyncio.Event()

    async def pre_run(self):
        # dataset, pandas and jsonlines is needed
        import_packages(["datasets", "jsonlines", "pandas"])
        # transformers needed
        try:
            import transformers
        except ImportError:
            import_package(package_name="transformers", version="4.57.1")

        # check llm_config for planning or other high-level tasks,
        if not self.conf.llm_config:
            if not os.environ.get("LLM_MODEL_NAME") or not os.environ.get("LLM_API_KEY"):
                raise ValueError("llm_config or env vars must be set")
            model_conf = ModelConfig(
                llm_model_name=os.getenv('LLM_MODEL_NAME'),
                llm_api_key=os.getenv('LLM_API_KEY'),
                llm_base_url=os.getenv('LLM_BASE_URL', "https://api.openai.com/v1"),
                llm_provider=os.getenv('LLM_PROVIDER', "openai"),
            )
            self.conf.llm_config = model_conf
        else:
            if not os.environ.get("LLM_MODEL_NAME") or not os.environ.get("LLM_API_KEY"):
                model_conf = self.conf.llm_config
                os.environ['LLM_API_KEY'] = model_conf.llm_api_key
                os.environ['LLM_PROVIDER'] = model_conf.llm_provider
                os.environ['LLM_BASE_URL'] = model_conf.llm_base_url
                os.environ['LLM_MODEL_NAME'] = model_conf.llm_model_name

    async def do_run(self):
        # supported text2training pipeline only now
        #### Plan: tool_synthesis -> tool_verify -> sample_synthesis -> sample_verify -> train -> evaluate
        # Create evolve yaml
        logger.info(f"Evolution plan start...")
        await self.outputs.add_output(Output(data="Evolution start...", metadata={"title": "Evolution"}))
        res = await Runners.run(input=self.task, agent=EvolutionPipelineAgent(conf=AgentConfig(**self.conf.to_dict())))
        plan = res.answer

        if isinstance(plan, str):
            # task:
            # config_path:
            # config: {"project_name": "", "workspace": "", "max_epoches": 1, "model":"", "process_tasks": []}
            plan = json.loads(plan)
        config = plan.get("config", {})
        task = plan.get("task")
        dir_name = config.get("workspace", "spec")

        # todo: in agent and auto modify
        await self.human_confirm(
            content=f"Please confirm the generated plan and configuration `evolve_config.yaml` in {os.path.abspath(dir_name)}."
                    f"\nIt may be necessary to modify the model path, etc",
            hitl=self.conf.hitl_plan
        )

        config = load_config("evolve_config.yaml", dir_name=dir_name)
        logger.info(f"Evolution plan finished, result: {plan}")
        process_tasks = config.get("process_tasks", ["sample_synthesis", "train"])

        render_config = await self._render_config(plan, process_tasks)

        render_con = evolution_plan_render(render_config)
        logger.info(f"Evolution plan: \n{render_con}")
        await self.outputs.add_output(
            Output(data=f"Evolution plan finished.\n {render_con}",
                   metadata={"print_all": True, "title": "Evolution Plan"}))

        # default minimum workflow

        epoches = config.get("max_epoches", 1)
        for epoch in range(epoches):
            #### Data Synthesis
            logger.info(f"Epoch {epoch} start dataset synthesis...")
            train_synthesis_data, test_synthesis_data = await self.data_synthesis(epoch=epoch,
                                                                                  task=task,
                                                                                  dir_name=dir_name,
                                                                                  process_tasks=process_tasks)
            # Keep tools for next epoch
            if "tool_synthesis" in process_tasks:
                process_tasks.remove("tool_synthesis")

            #### Training
            logger.info(f"Epoch {epoch} start training...")
            # TODO: train agent create train.yaml with some parameters

            # train can not skip
            await self.train(epoch=epoch,
                             evolve_config=config,
                             train_dataset_file=train_synthesis_data,
                             test_dataset_file=test_synthesis_data)
            #### Evaluation
            if "evaluate" in process_tasks:
                logger.info(f"Epoch {epoch} start evaluating...")
                await self.evaluation(epoch=epoch, dir_name=dir_name, test_dataset_file=test_synthesis_data)

            logger.info(f"Epoch {epoch} finished")
        logger.info(f"Evolution pipeline finished!")
        await self.outputs.add_output(Output(data=f"Evolution pipeline finished! \n"
                                                  f"Please check dir: {os.path.abspath(dir_name)}",
                                             metadata={"print_all": True, "title": "Evolution"}))
        return TaskResponse(answer=f"Evolution pipeline finished, please check dir: {os.path.abspath(dir_name)}")

    async def evaluation(self, epoch: int, dir_name: str, test_dataset_file: str):
        """Run evaluation on the test dataset and save results."""
        if not test_dataset_file or not os.path.exists(test_dataset_file):
            logger.warning(f"Test dataset file not found: {test_dataset_file}")
            return

        if not hasattr(self, "trainer"):
            logger.warning(f"Need to complete the training first!")
            return

        await self.outputs.add_output(Output(data=f"Start epoch {epoch} evaluation...",
                                             metadata={"title": "Evaluation"}))

        metrics = await self.trainer.inference()

        try:
            result_file = os.path.join(dir_name, 'evaluation_result.json')
            with open(result_file, 'w') as f:
                json.dump(metrics, f, indent=4, ensure_ascii=False)
            logger.info(f"Evaluation finished. Results saved to {result_file}")
            await self.human_confirm(content=f"Please confirm evaluation results in {result_file}")
        except Exception as e:
            logger.error(f"Failed to save evaluation result: {e}")

        await self.outputs.add_output(Output(data=f"Finished epoch {epoch} evaluation. \nResults: {metrics}",
                                             metadata={"title": "Evaluation"}))

    async def train(self, epoch: int, evolve_config: Dict[str, Any], train_dataset_file: str, test_dataset_file: str):
        """Train process.

        Args:
            evolve_config: Evolve config.
            train_dataset_file: Train dataset file path.
            test_dataset_file: Test dataset file path.
        """

        await self.outputs.add_output(Output(data=f"Start epoch {epoch} training...",
                                             metadata={"title": "Training"}))

        dir_name = evolve_config.get("workspace", "spec")
        # todo: create train.yaml by agent
        configs = load_config(dir_name=dir_name, file_name='train.yaml')
        if "dir_name" not in configs:
            configs['dir_name'] = dir_name
        if "model" not in configs:
            configs['model'] = evolve_config.get('model')

        train_engine_name = configs.get('train_framework')
        if train_engine_name not in TRAIN_PROCESSOR:
            train_engine_name = 'trl'

        # data format transform
        if not configs.get('train_dataset', None):
            train_dataset = await self._convert_dataset(train_dataset_file, train_framework=train_engine_name)
            configs['train_dataset'] = train_dataset

        if not configs.get('test_dataset', None):
            test_dataset = await self._convert_dataset(test_dataset_file, train_framework=train_engine_name)
            configs['test_dataset'] = test_dataset

        # config generate
        config = await self._generate_config(configs=configs, train_framework=train_engine_name)

        # Agent create
        agents_dict = configs.get("agents", {})
        agent = await self._generate_agent(agents_dict)

        # reward generate
        reward_func = await self._generate_reward_fn(train_framework=train_engine_name)

        train_dataset, test_dataset = configs['train_dataset'], configs['test_dataset']
        trainer = AgentTrainer(agent=agent,
                               config=config,
                               reward_func=reward_func,
                               train_dataset=train_dataset,
                               test_dataset=test_dataset,
                               train_engine_name=train_engine_name,
                               run_path=dir_name)
        trainer.train()
        self.trainer = trainer
        await self.outputs.add_output(Output(data=f"Finished epoch {epoch} training. ",
                                             metadata={"title": "Training"}))

    async def _convert_dataset(self, input_file: str, train_framework: str):
        import jsonlines

        if train_framework == 'verl':
            datas = []
            with jsonlines.open(input_file) as reader:
                for item in reader:
                    datas.append({
                        "prompt": [{"role": "user", "content": item['task']}],
                        "data_source": "synthesis_dataset",
                        "ability": "self_evolve",
                        "reward_model": {'ground_truth': item['answer'], 'style': 'synthesis'},
                        "extra_info": {"id": ""},
                        "agent_name": "evolve_agent"
                    })
            df = pd.DataFrame(datas)
            dataset_file = os.path.splitext(input_file)[0] + '.parquet'
            df.to_parquet(dataset_file, index=False)
        elif train_framework == 'trl':
            datas = []
            with jsonlines.open(input_file) as reader:
                for item in reader:
                    datas.append({"prompt": [{"role": "user", "content": item['task']}], "solution": item['answer']})

            dataset_file = f"{os.path.dirname(input_file)}/trl_{os.path.basename(input_file)}"
            with jsonlines.open(dataset_file, mode='w') as writer:
                writer.write_all(datas)
        else:
            dataset_file = ''
        return dataset_file

    async def _generate_config(self, configs: Dict[str, Any], train_framework: str) -> str:
        """Generate train config.

        Args:
            train_framework: Name of train framework.

        Returns:
            Train config.
        """
        train_configs = TRAIN_DEFAULT_CONFIG.get(train_framework)
        if train_framework == 'verl':
            logger.info(
                "VeRL relies on multiple modules, please confirm in advance that the relevant dependencies have been installed")

            train_configs['reward_model']['model']['path'] = configs.get('reward_model')
            train_configs['trainer']['default_local_dir'] = configs.get('dir_name')
            train_configs['actor_rollout_ref']['model']['path'] = configs.get('model')
        elif train_framework == 'trl':
            logger.info("Auto check the dependent modules of TRL.")
            import_packages(["scipy", "trl"])

            train_configs['model'] = configs.get('model')
            train_configs['reward_model'] = configs.get('reward_model', configs.get('model'))
            train_configs['output_dir'] = configs.get('dir_name')

        train_config_file = f'{configs.get("dir_name")}/train_config.yaml'
        with open(train_config_file, 'w') as yaml_file:
            yaml.dump(train_configs, yaml_file, default_flow_style=False, indent=4)
        return train_config_file

    async def _generate_agent(self, configs: dict) -> Union[str, Agent, Swarm]:
        """Generate agent.

        Args:
            configs: Configuration of agent.

        Returns:
            Agent.
        """
        agents = load_agents_from_dict(configs)
        if not agents:
            logger.warning("No agent found")
            return "virtual"
        # use one agent now
        agent = list(agents.values())[0]
        # name is "evolve_agent"
        agent._name = "evolve_agent"
        return agent

    async def _generate_reward_fn(self, train_framework: str):
        if train_framework == 'trl':
            # default process
            return None
        else:
            return verl_default_reward_func

    async def data_synthesis(self, epoch: int, task: Any, dir_name: str, process_tasks: List[str]):
        await self.outputs.add_output(Output(data=f"Start epoch {epoch} dataset synthesis...",
                                             metadata={"title": "Data Synthesis"}))
        # tool synthesis
        tool_data_file = None
        if "tool_synthesis" in process_tasks:
            configs = load_config(dir_name=dir_name, file_name='data_synthesis.yaml')
            tool_synthesis_config = DataSynthesisConfig(**configs)
            tool_synthesis_config.gen_tasks = False
            tool_synthesis_config.gen_tools = True

            # eval synthesis tool
            if "tool_verify" in process_tasks:
                tool_synthesis_config.eval_data = True
            if not tool_synthesis_config.dir_name:
                tool_synthesis_config.dir_name = dir_name
            if not tool_synthesis_config.llm_config:
                tool_synthesis_config.llm_config = self.conf.llm_config

            if not isinstance(task, Task):
                task = Task(input=task, context=getattr(self.task, "context", Context()))

            runner = DataSynthesisRunner(task=task, conf=tool_synthesis_config)
            # choose special runtime engine
            engine: RuntimeEngine = await runtime_engine(self.conf.run_conf)
            res = await engine.execute([runner.run])
            # no id rsult, key is "0"
            _, _, tool_data_file = res.get("0")
            await self.human_confirm(content=f"Please confirm the tool list in {tool_data_file}")

        # sample synthesis
        train_synthesis_data, test_synthesis_data = None, None
        configs = load_config(dir_name=dir_name, file_name='data_synthesis.yaml')
        data_synthesis_config = DataSynthesisConfig(**configs)
        data_synthesis_config.llm_config = self.conf.llm_config
        data_synthesis_config.dir_name = dir_name
        if "sample_synthesis" not in process_tasks:
            postfix = data_synthesis_config.task_file_postfix
            if os.path.exists(os.path.join(dir_name, f"train_{postfix}")):
                train_synthesis_data = os.path.join(dir_name, f"train_{postfix}")
            if os.path.exists(os.path.join(dir_name, f"test_{postfix}")):
                test_synthesis_data = os.path.join(dir_name, f"test_{postfix}")

        if not train_synthesis_data:
            if tool_data_file:
                data_synthesis_config.tool_file_name = os.path.basename(tool_data_file)
            # eval synthesis task
            if "sample_verify" in process_tasks:
                data_synthesis_config.eval_data = True

            runner = DataSynthesisRunner(task=self.task, conf=data_synthesis_config)
            # choose special runtime engine
            engine: RuntimeEngine = await runtime_engine(self.conf.run_conf)
            res = await engine.execute([runner.run])
            # no id rsult, key is "0"
            train_synthesis_data, test_synthesis_data, _ = res.get("0")

            await self.human_confirm(
                content=f"Please confirm the dataset in {train_synthesis_data}, {test_synthesis_data}"
            )
        df = pd.read_json(train_synthesis_data, lines=True)
        await self.outputs.add_output(Output(data=f"Finished epoch {epoch} dataset synthesis. "
                                                  f"Data examples: \n{df.loc[:, ['task', 'answer']].head()}",
                                             metadata={"title": "Data Synthesis"}))
        return train_synthesis_data, test_synthesis_data

    async def _render_config(self, config: dict, process_tasks: List[str]):
        render_config = {
            "goal": hasattr(self.task, "input") and self.task.input or str(self.task),
            "agent_loop": " → ".join(process_tasks),
            "subagents": [

            ],
            "skills": [

            ],
            "tools": {
                "built_in": [],
                "mcp": [''],
                "custom": [],
            },
            "model": [],
            "plan_output": [],
        }
        if "tool_synthesis" in process_tasks or "sample_synthesis" in process_tasks:
            render_config["skills"].append("data_synthesis")
            render_config["tools"]["custom"].append("imitation_tool")
            render_config["subagents"].extend(["tool_generator_agent", "tool_select_agent", "task_generator_agent"])
        if self.conf.hitl_plan or self.conf.hitl_all:
            render_config["tools"]["built_in"].append("human-in-the-loop")
        render_config["model"].append(f'training model: {config.get("config", {}).get("model", "")}')
        render_config["model"].append(f'evaluation model: {config.get("config", {}).get("eval_model", "")}')

        render_config['plan_output'].append("{")
        for k, v in config.items():
            if k != 'config':
                con = f"  {k}: {v}"
                if len(con) > 120:
                    con = con[0:120] + "\n" + con[120:]
                    render_config['plan_output'].append(con[0:120])
                    render_config['plan_output'].append(con[120:])
                else:
                    render_config['plan_output'].append(con)
            else:
                con = f"  config: {os.path.abspath(config.get('config', {}).get('dir_name', '.'))}/evolve_config.yaml"
                if len(con) > 120:
                    render_config['plan_output'].append(con[0:120])
                    render_config['plan_output'].append(con[120:])
                else:
                    render_config['plan_output'].append(con)
        render_config['plan_output'].append("}")
        return render_config

    async def human_confirm(self, content: str, hitl: bool = None):
        """Human confirm.

        Args:
            content: Confirm content.
        """
        if hitl is None:
            hitl = self.conf.hitl_all
        if hitl:
            logger.info("Waiting for confirmation...\nContinue only after receiving confirmed input")
            await self.outputs.add_output(Output(data=f"Waiting for confirmation...\n"
                                                      f"Continue only after receiving confirmed input"))
            await exec_tool(tool_name=HUMAN,
                            action_name="HUMAN_CONFIRM",
                            params={"confirm_content": content},
                            agent_name="human",
                            context=Context())
