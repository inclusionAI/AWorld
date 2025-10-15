import os
import time
import traceback
from typing import Any, Dict, List, Optional

from aworld.memory.main import MemoryFactory
from aworld.memory.models import MemorySystemMessage, MessageMetadata
from ... import ApplicationContext
from ...event import SystemPromptEvent
from aworld.logs.util import logger
from .base import BaseOp, MemoryCommand
from .op_factory import memory_op
from ...prompt.neurons import neuron_factory, Neuron
from ...prompt.prompt_ext import ContextPromptTemplate
from ...retrieval.reranker import RerankResult
from ...retrieval.reranker.factory import RerankerFactory


@memory_op("system_prompt_augment")
class SystemPromptAugmentOp(BaseOp):
    """
    System prompt formatting operator
    Uses ContextPromptTemplate to enhance and format system prompts
    Integrates prompt component processing logic, supporting both rerank and append strategies
    """

    def __init__(self, name: str = "system_prompt_augment", **kwargs):
        super().__init__(name, **kwargs)
        self._memory = MemoryFactory.instance()

    async def execute(self, context: ApplicationContext, info: Dict[str, Any] = None, event: SystemPromptEvent = None,
                      **kwargs) -> Dict[str, Any]:
        try:
            # if system prompt existed, return
            if await self.check_system_prompt_existed(context, event):
                return {
                    "memory_commands": []
                }

            # get memory commands
            if info:
                memory_commands = info.get("memory_commands", [])
            else:
                memory_commands = []

            # process prompt components
            augment_prompts = await self._process_neurons(context, event)

            # build system message command and return
            system_command = await self.build_system_command(context, event, augment_prompts)
            memory_commands.append(system_command)
            return {
                "memory_commands": memory_commands
            }

        except Exception as e:
            logger.error(f"System prompt format error: {e} {traceback.format_exc()}")
            return {
                "memory_commands": []
            }

    async def _process_neurons(self, context: ApplicationContext, event: SystemPromptEvent) -> str:
        """
        处理prompt组件，支持rerank和append两种策略
        支持根据namespace过滤component_neuron配置的组件
        """
        augment_prompts = {}

        # 记录每个组件的耗时
        component_timings = []
        total_start_time = time.time()

        # 获取namespace（从event中获取）
        namespace = getattr(event, 'namespace', None)
        agent_id = getattr(event, 'agent_id', None)

        # 处理组件
        neurons = neuron_factory.get_all_neurons(namespace=namespace)

        # 处理rerank策略的组件
        if neurons:
            # desc
            for neuron in neurons:
                augment_prompts[neuron.name] = await neuron.desc(context=context, namespace=namespace)

            # context

            for neuron in neurons:
                component_start_time = time.time()
                component_name = neuron.__class__.__name__

                try:
                    # context augment
                    st = time.time()
                    augment_prompts[neuron.name] = (augment_prompts[neuron.name] + '\n\n'
                                                    + await self.rerank_items(neuron=neuron,
                                                                   context=context, namespace=namespace))
                    t1 = time.time() - st
                    logger.debug(
                        f"_process_prompt_components rerank strategy: {component_name} rerank time: start_time={st}s format_time={t1:.3f}s")

                    component_end_time = time.time()
                    component_duration = component_end_time - component_start_time
                    component_timings.append(f"{component_name}:{component_duration:.3f}s")

                except Exception as e:
                    component_end_time = time.time()
                    component_duration = component_end_time - component_start_time
                    component_timings.append(f"{component_name}:{component_duration:.3f}s(error)")
                    logger.error(f"Error processing rerank component {component_name}: {e} {traceback.format_exc()}")

        total_end_time = time.time()
        total_duration = total_end_time - total_start_time

        # 格式化耗时信息
        timing_info = f"total:{total_duration:.3f}s, " + ", ".join(component_timings)

        logger.info(
            f"Successfully processed {len(augment_prompts)} prompt components for agent {event.agent_id}, session {context.session_id}, timings: {timing_info}")

        return augment_prompts

    async def rerank_items(self, neuron: Neuron, context: ApplicationContext,
                           namespace: str) -> str:
        user_query = context.task_input

        st = time.time()
        reranker = RerankerFactory.get_default_reranker()
        score_threshold = self._get_score_threshold()
        items = await neuron.format_items(context=context, namespace=namespace)
        t1 = time.time() - st
        # 只有当items不为空时才进行rerank
        if not items:
            return ""

        # 长度不够不需要rerank 直接append
        total_length = sum(len(item) for item in items)
        if total_length <= 4000:
            return await neuron.format(context=context, namespace=namespace)

        # 只判断前面一部分文本
        tmp_items = [item[:1000] for item in items]
        rerank_results = await reranker.run(query=user_query, documents=tmp_items)
        t2 = time.time() - st - t1
        if rerank_results:
            # 过滤分数大于阈值的文档
            filtered_results = self._filter_by_score(rerank_results, score_threshold)
            if filtered_results:
                # 从过滤后的rerank结果中提取文档内容
                reranked_docs = self._filter_items_by_rerank_result(items, rerank_results)
                component_prompt = await neuron.format(context=context, items=reranked_docs, namespace=namespace)

                logger.debug(f"Component {neuron}: "
                             f"filtered {len(filtered_results)}/{len(rerank_results)} docs "
                             f"with threshold {score_threshold}")
                return component_prompt
        t3 = time.time() - st - t1 - t2
        logger.info(
            f"_process_prompt_components: {neuron.__class__.__name__} rerank time: start_time={st}s format_time={t1:.3f}s, rerank_time={t2:.3f}s, filter_time={t3:.3f}s lens={[len(item) for item in items]}")
        return ""

    def _filter_items_by_rerank_result(self, items: List[str], rerank_results: RerankResult) -> List[str]:
        filtered = []
        target_ids = [rr.idx for rr in rerank_results]
        for i, item in enumerate(items):
            if i in target_ids:
                filtered.append(item)
        return filtered

    def _get_score_threshold(self) -> float:
        try:
            threshold_str = os.environ.get('RERANKER_SCORE_THRESHOLD', '0.0')
            threshold = float(threshold_str)
            logger.debug(f"Using reranker score threshold: {threshold}")
            return threshold
        except (ValueError, TypeError) as e:
            logger.warning(
                f"Invalid RERANKER_SCORE_THRESHOLD value: {os.environ.get('RERANKER_SCORE_THRESHOLD')}, using default 0.0. Error: {e}")
            return 0.0

    def _filter_by_score(self, rerank_results: List, score_threshold: float) -> List:
        if not rerank_results:
            return []

        filtered_results = []
        for result in rerank_results:
            # 检查结果对象是否有score属性
            if hasattr(result, 'score') and result.score is not None:
                if result.score > score_threshold:
                    filtered_results.append(result)
                else:
                    logger.debug(f"Filtered out document with score {result.score} (threshold: {score_threshold})")
            else:
                # 如果没有score属性，保留该结果（向后兼容）
                logger.warning(f"Rerank result missing score attribute, keeping result: {result}")
                filtered_results.append(result)

        return filtered_results

    async def build_system_command(self, context: ApplicationContext, event: SystemPromptEvent, augment_prompts: str) -> Optional[MemoryCommand]:
        """
        build system message command
        """
        agent_id = event.agent_id
        agent_name = event.agent_name
        user_query = event.user_query

        # combine system prompt and augment_prompts
        appended_prompt = event.system_prompt + "\n\n" + "\n".join(augment_prompts.values())

        formatted_system_prompt = await ContextPromptTemplate(template=appended_prompt).async_format(
            context=context,
            task=user_query)
        # if not exist history, add new system message
        system_message = await self._build_system_message(
            context=context,
            content=formatted_system_prompt,
            agent_id=agent_id,
            agent_name=agent_name
        )

        return MemoryCommand(
            type="ADD",
            item=system_message,
            memory_id=None
        )

    async def check_system_prompt_existed(self, context, event):
        session_id = context.get_task().session_id
        task_id = context.get_task().id
        #  check history
        histories = self._memory.get_last_n(0, filters={
            "agent_id": event.agent_id,
            "session_id": session_id,
            "task_id": task_id
        })
        return histories and len(histories) > 0

    async def _build_system_message(self,
                                    context: ApplicationContext,
                                    content: str,
                                    agent_id: str,
                                    agent_name: str = None) -> MemorySystemMessage:
        session_id = context.get_task().session_id
        task_id = context.get_task().id
        user_id = context.get_task().user_id

        system_message = MemorySystemMessage(
            content=content,
            metadata=MessageMetadata(
                session_id=session_id,
                user_id=user_id,
                task_id=task_id,
                agent_id=agent_id,
                agent_name=agent_name or 'unknown',
            )
        )

        return system_message
