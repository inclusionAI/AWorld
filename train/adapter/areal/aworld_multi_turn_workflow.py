import asyncio
import uuid
import os
from transformers import PreTrainedTokenizerFast
from areal.api.cli_args import GenerationHyperparameters
from areal.api.engine_api import InferenceEngine
from areal.api.io_struct import ModelResponse

from aworld.core.task import Task
from aworld.config.conf import TaskConfig
from aworld.runner import Runners

from .aworld_workflow import AworldWorkflow


class AworldMultiTurnWorkflow(AworldWorkflow):
    def __init__(
        self,
        reward_fn,
        gconfig: GenerationHyperparameters,
        tokenizer: PreTrainedTokenizerFast,
        max_turns: int,
        turn_discount: float,
        rollout_stat_scope: str = "rollout",
        dump_dir: str | None = None,
    ):
        super().__init__(reward_fn, gconfig, tokenizer, enable_thinking=False, rollout_stat_scope=rollout_stat_scope, dump_dir=dump_dir)
        self.max_turns = max_turns
        self.turn_discount = turn_discount

    async def _run_one_episode(self, engine: InferenceEngine, data, rid):
        agent = await self.build_agents(engine)
        aworld_task = Task(input=data["messages"][0].get("content"),
                           agent=agent,
                           conf=TaskConfig(resp_carry_context=False, resp_carry_raw_llm_resp=True))

        seq, logprobs, loss_mask, versions = [], [], [], []
        # messages = data["messages"]
        # input_ids = self.tokenizer.apply_chat_template(
        #     messages,
        #     tokenize=True,
        #     add_generation_prompt=True,
        # )

        # Run multi-turn rollout until correct
        t = reward = 0
        discount = 1
        while reward == 0 and t < self.max_turns:
            # Send generate request to get the response.

            response = await Runners.run_task(aworld_task)
            resp = response.items[0].value
            model_output: ModelResponse = resp.raw_llm_resp.raw_response

            # req = ModelRequest(
            #     rid=rid,
            #     input_ids=input_ids,
            #     gconfig=self.gconfig.new(n_samples=1),
            #     tokenizer=self.tokenizer,
            # )
            # resp = await engine.agenerate(req)
            # compute reward: 1 for correct and 0 otherwise
            prompt_str = self.tokenizer.decode(model_output.prompt_ids)
            completions_str = self.tokenizer.decode(model_output.output_tokens)
            reward = await self.async_reward_fn(
                prompt_str,
                completions_str,
                model_output.input_tokens,
                model_output.output_tokens,
                **data,
            )
            # Amend results
            input_len = len(model_output.input_tokens) - len(seq)
            assert len(seq) == 0 or model_output.input_tokens[:-input_len] == seq, (
                seq,
                model_output.input_tokens[:-input_len],
                len(seq),
                len(model_output.input_tokens[:-input_len]),
            )
            seq += model_output.input_tokens[-input_len:] + model_output.output_tokens
            logprobs += [0.0] * input_len + model_output.output_logprobs
            loss_mask += [0] * input_len + [1] * model_output.output_len
            versions += [-1] * input_len + model_output.output_versions
            # Increase counter
            t += 1
            # Amend a prompt if the previous answer is incorrect
            if reward == 0 and t < self.max_turns:
                input_ids = input_ids + model_output.output_tokens
                if model_output.output_tokens[-1] != self.tokenizer.eos_token_id:
                    input_ids += [self.tokenizer.eos_token_id]
                input_ids += self.multi_turn_prompt_ids
                discount *= self.turn_discount

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
            res,
            prompt_str,
            completions_str,
            reward,
            len(seq),
        )

    async def arun_episode(self, engine: InferenceEngine, data):
        """Run a single episode of the AWorld environment."""
        rid = uuid.uuid4().hex
        tasks = [
            self._run_one_episode(engine, data, rid)
            for _ in range(self.gconfig.n_samples)
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
