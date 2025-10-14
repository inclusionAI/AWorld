import os
import time
import traceback
from typing import Any, Dict, List

from ... import ApplicationContext
from ...event import SystemPromptEvent
from aworld.logs.util import logger
from .base import BaseOp
from .op_factory import memory_op
from ...prompt.neurons import neuron_factory, Neuron
from ...retrieval.reranker import RerankResult
from ...retrieval.reranker.factory import RerankerFactory


@memory_op("neuron_augment")
class NeuronAugmentOp(BaseOp):
    """
    系统提示词格式化算子
    使用ContextPromptTemplate对系统提示词进行增强和格式化
    融合了prompt组件的处理逻辑，支持rerank和append两种策略
    """

    def __init__(self, name: str = "neuron_augment", **kwargs):
        super().__init__(name, **kwargs)

    async def execute(self, context: ApplicationContext, **kwargs) -> Dict[str, Any]:
        try:
            event: SystemPromptEvent = kwargs.get("event", None)

            # 处理prompt组件
            augment_prompts = await self._process_neurons(context, event)

            return {
                "augment_prompts": augment_prompts,
            }
        except Exception as e:
            # 出错返回空白
            logger.error(f"System prompt format error: {e} {traceback.format_exc()}")
            return {
                "augment_prompts": {},
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
