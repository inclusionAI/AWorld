# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import asyncio
import concurrent
import random
import os
import uuid
from typing import Union

import aiofiles
import aiofiles.os
import colorama
import torch
from aworld.config.conf import TaskConfig, AgentConfig

from aworld.agents.llm_agent import Agent
from aworld.core.task import Task
from aworld.core.agent.swarm import Swarm
from aworld.logs.util import logger
from aworld.runner import Runners
from aworld.utils.async_func import start_loop, use_new_loop, shutdown_all

from tensordict import TensorDict
from transformers import PreTrainedTokenizerFast

from areal.api.cli_args import GenerationHyperparameters
from areal.api.engine_api import InferenceEngine
from areal.api.io_struct import ModelResponse
from areal.api.reward_api import AsyncRewardWrapper
from areal.api.workflow_api import RolloutWorkflow
from areal.utils import stats_tracker
from areal.utils.data import concat_padded_tensors
from areal.workflow.areal_provider import ArealProvider

THREAD_POOL = None
LOOP = []


def create_pool(workers: int = 256):
    import asyncio
    import concurrent

    assert workers > 0, "workers value must large than 0"
    THREAD_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=workers)

    for i in range(workers):
        new_loop = asyncio.new_event_loop()
        LOOP.append(new_loop)
        THREAD_POOL.submit(start_loop, new_loop)


def close_pool():
    shutdown_all(LOOP)
    if THREAD_POOL:
        THREAD_POOL.shutdown()


class AworldWorkflow(RolloutWorkflow):
    def __init__(
            self,
            reward_fn,
            gconfig: GenerationHyperparameters,
            tokenizer: PreTrainedTokenizerFast,
            enable_thinking: bool,
            rollout_stat_scope: str = "rollout",
            dump_dir: str | None = None,
    ):
        self.reward_fn = reward_fn
        self.gconfig = gconfig
        self.tokenizer = tokenizer
        self.enable_thinking = enable_thinking
        self.dump_dir = dump_dir
        self.rollout_stat_scope = rollout_stat_scope
        self.async_reward_fn = AsyncRewardWrapper(reward_fn)
        if self.dump_dir is not None and not os.path.exists(self.dump_dir):
            os.makedirs(self.dump_dir, exist_ok=True)

    @abc.abstractmethod
    async def build_agents(self, engine) -> Union[Agent, Swarm]:
        """Build single- or multi-agent"""

    async def run_task(self, tasks):
        if not use_new_loop:
            return await Runners.run_task(tasks)
        else:
            idx = random.randint(0, len(LOOP) - 1)
            logger.info(f"loop {idx} tasks: {len(asyncio.all_tasks(LOOP[idx]))}")
            con_future = asyncio.run_coroutine_threadsafe(Runners.run_task(tasks), LOOP[idx])
            return await asyncio.wrap_future(con_future)

    async def arun_episode(self, engine: InferenceEngine, data):
        n_samples = self.gconfig.n_samples
        tasks = [Task(input=data["messages"][0].get("content"),
                      agent=await self.build_agents(engine),
                      conf=TaskConfig(resp_carry_raw_llm_resp=True, resp_carry_context=False))
                 for _ in range(n_samples)]
        task_dict = {task.id: task for task in tasks}
        responses = await self.run_task(tasks)
        version = engine.get_version()
        prompt_strs = []
        completions_strs = []
        rewards = []
        seqlens = []

        results = []
        prompts_ids = self.tokenizer.apply_chat_template(
            data["messages"],
            tokenize=True,
            add_generation_prompt=True,
            enable_thinking=self.enable_thinking,
        )

        for key, resp in responses.items():
            model_output: ModelResponse = resp.raw_llm_resp.raw_response

            seq = model_output.input_tokens + model_output.output_tokens
            logprobs = [0.0] * model_output.input_len + model_output.output_logprobs
            loss_mask = [0] * model_output.input_len + [1] * model_output.output_len
            versions = [-1] * model_output.input_len + model_output.output_versions

            prompt_str = self.tokenizer.decode(prompts_ids)
            completions_str = self.tokenizer.decode(model_output.output_tokens)
            prompt_strs.append(prompt_str)
            completions_strs.append(completions_str)
            seqlens.append(len(seq))

            reward = await self.async_reward_fn(
                completions_str,
                **data,
            )

            # Log reward.
            stats_tracker.get(self.rollout_stat_scope).scalar(reward=reward)
            rewards.append(reward)
            res = dict(
                # unsqueeze to add an additional batch dimension
                input_ids=torch.tensor(seq).unsqueeze(0),
                loss_mask=torch.tensor(loss_mask).unsqueeze(0),
                logprobs=torch.tensor(logprobs).unsqueeze(0),
                versions=torch.tensor(versions).unsqueeze(0),
                attention_mask=torch.ones(len(seq), dtype=torch.bool).unsqueeze(0),
                # reward
                rewards=torch.tensor([float(reward)]),
            )
            results.append(TensorDict(res, batch_size=[1]))

        if self.dump_dir is not None:
            dump_path = os.path.join(self.dump_dir, str(version))
            await aiofiles.os.makedirs(dump_path, exist_ok=True)
            # Get the unique identifier for this prompt
            for key in ["query_id", "id", "qid"]:
                qid = data.get(key, None)
                if qid is not None:
                    break
            qid = qid or uuid.uuid4().hex

            # Dump rollout to file
            file_path = os.path.join(dump_path, f"{qid}.txt")
            async with aiofiles.open(file_path, "a") as f:
                n_samples = self.gconfig.n_samples
                for i, (p, c, r, sl) in enumerate(
                        zip(prompt_strs, completions_strs, rewards, seqlens)
                ):
                    info = "\n".join(
                        [
                            f"idx: {i + 1} / {n_samples}, seqlen: {sl}, reward is {r}.",
                            f"prompt is \n{colorama.Fore.YELLOW + colorama.Style.DIM}{p}{colorama.Style.RESET_ALL}",
                            f"sequence is: \n{colorama.Fore.YELLOW + colorama.Style.DIM}{c}{colorama.Style.RESET_ALL}",
                        ]
                    )
                    await f.write(info + "\n")

        res = concat_padded_tensors(results)
        return res
