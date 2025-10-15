import time
import traceback
from typing import Dict, List, Any, Optional

from ..config import AmniContextProcessorConfig
from ..event import Event
from aworld.logs.util import logger
from .op.op_factory import OpFactory
from .processor_factory import memory_processor, BaseContextProcessor
from aworld.core.context.base import Context


@memory_processor("pipeline_memory_processor")
class PipelineMemoryProcessor(BaseContextProcessor):
    
    def __init__(self, processor_config: AmniContextProcessorConfig):
        super().__init__(processor_config)
        self.ops = self._get_ops(processor_config)
    
    def parse_pipeline_config(self, pipeline: str) -> List[str]:
        if not pipeline:
            return []
        
        # parse pipeline string, support comma-separated operations
        # Example: "set_query|retrieve_top_memory|print_memory"
        pipeline_parts = [part.strip() for part in pipeline.split("|") if part.strip()]
        return pipeline_parts

    def _get_ops(self, processor_config: AmniContextProcessorConfig) -> Dict[str, Any]:
        """get all registered operations from OpFactory"""
        ops = {}
        for op_name in OpFactory.list_all_ops():
            op_instance = OpFactory.create(op_name)
            if op_instance:
                ops[op_name] = op_instance
        return ops

    def build_pipeline(self, pipeline_config: str) -> List[Any]:
        """build operation list"""
        pipeline_ops = self.parse_pipeline_config(pipeline_config)

        ops_list = []
        for op_name in pipeline_ops:
            if op_name in self.ops:
                op_instance = self.ops[op_name]
                ops_list.append(op_instance)
            else:
                logger.warning(f"Unknown operation: {op_name}")
        
        if not ops_list:
            logger.warning("No valid operations found for pipeline")
            return []
        
        return ops_list
    
    async def execute_pipeline(self, ops_list: List[Any], context: Context, event: Event,  **kwargs) -> Optional[Dict[str, Any]]:
        """直接for循环执行操作列表"""
        pipeline_start_time = time.time()
        total_ops = len(ops_list)
        successful_ops = 0
        failed_ops = 0

        try:
            info = {}
            previous_result = None
            operation_metrics = []
            
            for i, op_instance in enumerate(ops_list):
                op_name = op_instance.__class__.__name__
                op_start_time = time.time()
                
                logger.debug(f"⚡ [{i+1}/{total_ops}] Executing: {op_name}")
                logger.debug(f"⚡ [{i+1}/{total_ops}] Executing: {op_name}")
                
                try:
                    # directly call the execute method of the operation
                    result = await op_instance.execute(context=context,
                                                      info=info,
                                                      event=event,
                                                      **kwargs)
                    
                    op_end_time = time.time()
                    op_duration = op_end_time - op_start_time
                    successful_ops += 1
                    
                    # record operation metrics
                    operation_metrics.append({
                        'operation': op_name,
                        'index': i + 1,
                        'duration': op_duration,
                        'status': 'success'
                    })
                    
                    # update info, ensure state transfer between operations
                    if result and isinstance(result, dict):
                        info.update(result)
                        previous_result = result
                        result_keys = list(result.keys())
                        logger.debug(f"✅ [{i+1}/{total_ops}] {op_name} completed in {op_duration:.3f}s | Updated info: {result_keys}")
                        logger.debug(f"✅ [{i+1}/{total_ops}] {op_name} completed in {op_duration:.3f}s | Updated info: {result_keys}")
                    else:
                        previous_result = result
                        logger.debug(f"✅ [{i+1}/{total_ops}] {op_name} completed in {op_duration:.3f}s | No info updates")
                        logger.debug(f"✅ [{i+1}/{total_ops}] {op_name} completed in {op_duration:.3f}s | No info updates")
                    
                except Exception as e:
                    op_end_time = time.time()
                    op_duration = op_end_time - op_start_time
                    failed_ops += 1
                    
                    # record failed operation metrics
                    operation_metrics.append({
                        'operation': op_name,
                        'index': i + 1,
                        'duration': op_duration,
                        'status': 'failed',
                        'error': str(e)
                    })
                    
                    logger.warn(f"❌ [{i+1}/{total_ops}] {op_name} failed in {op_duration:.3f}s | Error: {e} {traceback.format_exc()}")
                    logger.warning(f"❌ [{i+1}/{total_ops}] {op_name} failed in {op_duration:.3f}s | Error: {e} {traceback.format_exc()}")
                    # continue to execute the next operation, without interrupting the entire process
                    continue
            
            # calculate overall metrics
            pipeline_end_time = time.time()
            total_duration = pipeline_end_time - pipeline_start_time
            
            # record pipeline execution summary
            logger.debug(f"🏁 Pipeline execution completed:")
            logger.debug(f"   📊 Total duration: {total_duration:.3f}s")
            logger.debug(f"   ✅ Successful operations: {successful_ops}/{total_ops}")
            logger.debug(f"   ❌ Failed operations: {failed_ops}/{total_ops}")
            logger.debug(f"   📈 Success rate: {(successful_ops/total_ops)*100:.1f}%")

            # record each operation's detailed metrics
            if operation_metrics:
                logger.debug("📋 Operation metrics:")
                logger.debug("📋 Operation metrics:")
                for metric in operation_metrics:
                    status_icon = "✅" if metric['status'] == 'success' else "❌"
                    logger.debug(f"   {status_icon} [{metric['index']}] {metric['operation']}: {metric['duration']:.3f}s")
                    logger.debug(f"   {status_icon} [{metric['index']}] {metric['operation']}: {metric['duration']:.3f}s")
                    if metric['status'] == 'failed':
                        logger.warning(f"      Error: {metric['error']}")
                        logger.warning(f"      Error: {metric['error']}")
            
        
            return previous_result
            
        except Exception as e:
            pipeline_end_time = time.time()
            total_duration = pipeline_end_time - pipeline_start_time
            logger.warn(f"💥 Pipeline execution failed after {total_duration:.3f}s: {e}")
            logger.error(f"💥 Pipeline execution failed after {total_duration:.3f}s: {e}")
            return None
    
    async def process_with_pipeline(self, pipeline: str, context: Context, event: Event, **kwargs) -> Optional[Dict[str, Any]]:
        ops_list = self.build_pipeline(pipeline)
        if not ops_list:
            logger.warn("Failed to build pipeline")
            logger.warning("Failed to build pipeline")
            return None

        result = await self.execute_pipeline(ops_list=ops_list, context=context, event=event, **kwargs)

        return result

    async def process(self, context: Context, event: Event, **kwargs) -> Dict[str, Any]:
        """process message"""
        # use the configured pipeline to process
        return await self.process_with_pipeline(pipeline=self.config.pipeline, context=context, event=event, **kwargs)
