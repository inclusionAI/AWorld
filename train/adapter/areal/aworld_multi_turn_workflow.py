import asyncio
import os
import uuid

import aiofiles
import aiofiles.os
import colorama
import torch
from tensordict import TensorDict
from transformers import PreTrainedTokenizerFast

from typing import Union

from areal.api.cli_args import GenerationHyperparameters
from areal.api.engine_api import InferenceEngine
from areal.api.reward_api import AsyncRewardWrapper
from areal.api.workflow_api import RolloutWorkflow
from areal.utils import stats_tracker
from areal.utils.data import concat_padded_tensors

from aworld.agents.llm_agent import Agent
from aworld.core.agent.swarm import Swarm
from aworld.core.task import Task
from aworld.config.conf import AgentConfig, TaskConfig, TaskRunMode
from aworld.core.context.base import Context
from aworld.runner import Runners
from aworld.logs.util import logger


class AworldMultiTurnWorkflow(RolloutWorkflow):
    def __init__(
        self,
        reward_fn,
        gconfig: GenerationHyperparameters,
        tokenizer: PreTrainedTokenizerFast,
        max_turns: int,
        turn_discount: float,
        enable_thinking: bool,
        rollout_stat_scope: str = "rollout",
        dump_dir: str | None = None,
    ):
        self.reward_fn = reward_fn
        self.gconfig = gconfig
        self.tokenizer = tokenizer
        self.max_turns = max_turns
        self.turn_discount = turn_discount
        self.rollout_stat_scope = rollout_stat_scope
        self.async_reward_fn = AsyncRewardWrapper(reward_fn)
        self.dump_dir = dump_dir
        if self.dump_dir is not None and not os.path.exists(self.dump_dir):
            os.makedirs(self.dump_dir, exist_ok=True)
        self.enable_thinking = enable_thinking
        self.multi_turn_prompt = "Your answer is either wrong or not parsable to the reward function. You may misunderstand the original question. Please carefully read the original question, check the preivous errors, and try to answer it again."

    def build_agents(self, engine) -> Union[Agent, Swarm]:
        agent_config = AgentConfig(llm_base_url="dummy",
                                   llm_model_name="dummy",
                                   llm_provider="areal_rollout",
                                   params={"tokenizer": self.tokenizer, "enable_thinking": self.enable_thinking})
        agent = Agent(name="gaia", conf=agent_config)
        return agent

    async def _run_one_episode(self, engine: InferenceEngine, data, rid):
        # Enforces `n_samples=1`
        # Placeholders for the results
        seq, logprobs, loss_mask, versions = [], [], [], []
        messages = data["messages"]
        # Convert the prompt into input_ids
        input_ids = self.tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
        )
        # Run multi-turn rollout until correct
        t = reward = 0
        discount = 1
        context = Context()
        agent = self.build_agents(engine)
        task_id = rid
        aworld_task = Task(id=task_id,
                           input=data["messages"][0].get("content"),
                           agent=agent,
                           context=context,
                           conf=TaskConfig(resp_carry_context=True, run_mode=TaskRunMode.INTERACTIVE))
        while reward == 0 and t < self.max_turns:
            # Send generate request to get the response.
            responses = await Runners.run_task(aworld_task)
            resp = responses[task_id]
            context = resp.context
            step_token_ids = context.get_current_step_of_trajectory(agent.id())

            try:
                # compute reward: 1 for correct and 0 otherwise
                prompt_str = self.tokenizer.decode(step_token_ids.prompt_token_ids)
                completions_str = self.tokenizer.decode(step_token_ids.output_token_ids)
                reward = await self.async_reward_fn(
                    prompt_str,
                    completions_str,
                    step_token_ids.input_token_ids,
                    step_token_ids.output_token_ids,
                    **data,
                )
            except Exception:
                import traceback
                logger.error(f"compute reward: {traceback.format_exc()}")

            # Amend results
            input_len = len(step_token_ids.input_token_ids)
            seq += step_token_ids.input_token_ids + step_token_ids.output_token_ids
            logprobs += [0.0] * input_len + step_token_ids.output_logprobs
            loss_mask += [0] * input_len + [1] * len(step_token_ids.output_token_ids)
            versions += [-1] * input_len + step_token_ids.output_versions

            # Increase counter
            t += 1
            # Amend a prompt if the previous answer is incorrect
            if reward == 0 and t < self.max_turns:
                # input_ids = input_ids + resp.output_tokens
                # if resp.output_tokens[-1] != self.tokenizer.eos_token_id:
                #     input_ids += [self.tokenizer.eos_token_id]
                # input_ids += self.multi_turn_prompt_ids
                discount *= self.turn_discount
                if resp.status == "running":
                    aworld_task.observation = resp.answer
                else:
                    aworld_task.input = self.multi_turn_prompt

        reward = float(reward * discount)

        # Log reward.
        stats_tracker.get(self.rollout_stat_scope).scalar(reward=reward, num_turns=t)

        res = dict(
            input_ids=torch.tensor(seq),
            logprobs=torch.tensor(logprobs),
            loss_mask=torch.tensor(loss_mask),
            versions=torch.tensor(versions),
            rewards=torch.tensor(float(reward * discount)),
            attention_mask=torch.ones(len(seq), dtype=torch.bool),
        )
        res = {k: v.unsqueeze(0) for k, v in res.items()}
        return (
            TensorDict(res, batch_size=[1]),
            prompt_str,
            completions_str,
            reward,
            len(seq),
        )

    async def arun_episode(self, engine: InferenceEngine, data):
        tasks = [
            self._run_one_episode(engine, data, uuid.uuid4().hex)
            # for _ in range(self.gconfig.n_samples)
        ]
        results = await asyncio.gather(*tasks)

        if self.dump_dir is not None:
            version = engine.get_version()
            dump_path = os.path.join(self.dump_dir, str(version))
            await aiofiles.os.makedirs(dump_path, exist_ok=True)
            # Get the unique identifier for this prompt
            qid = None
            for key in ["query_id", "id", "qid"]:
                qid = data.get(key, None)
                if qid is not None:
                    break
            qid = qid or uuid.uuid4().hex

            # Dump rollout to file
            file_path = os.path.join(dump_path, f"{qid}.txt")
            async with aiofiles.open(file_path, "a") as f:
                n_samples = self.gconfig.n_samples
                for i, (_, p, c, r, sl) in enumerate(results):
                    info = "\n".join(
                        [
                            f"idx: {i + 1} / {n_samples}, seqlen: {sl}, reward is {r}.",
                            f"prompt is \n{colorama.Fore.YELLOW + colorama.Style.DIM}{p}{colorama.Style.RESET_ALL}",
                            f"sequence is: \n{colorama.Fore.YELLOW + colorama.Style.DIM}{c}{colorama.Style.RESET_ALL}",
                        ]
                    )
                    await f.write(info + "\n")

        data = [res[0] for res in results]
        return concat_padded_tensors(data)
