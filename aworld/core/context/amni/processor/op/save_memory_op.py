import traceback
from typing import Any, Dict

from aworld.logs.util import logger
from ... import ApplicationContext
from .base import BaseOp, MemoryCommand
from .op_factory import memory_op
from aworld.memory.main import MemoryFactory


@memory_op("save_memory")
class SaveMemoryOp(BaseOp):
    """SaveMemory"""

    def __init__(self, name: str = "save_memory", **kwargs):
        super().__init__(name, **kwargs)
        self._memory = MemoryFactory.instance()

    async def execute(self, context: ApplicationContext, info: Dict[str, Any] = None, **kwargs) -> Dict[str, Any]:
        memory_commands: list[MemoryCommand] = info.get("memory_commands")
        if not memory_commands:
            return {}

        for memory_command in memory_commands:
            try:
                if memory_command.type == "ADD":
                    await self._memory.add(memory_command.item, agent_memory_config = context.get_config().get_agent_memory_config(namespace = memory_command.item.agent_id))
                    logger.info(f"📝 add memory #{type(memory_command.item).__name__} -> {memory_command.item.id}")
                elif memory_command.type == "DELETE":
                    await self._memory.delete(memory_command.memory_id)
                    logger.info(f"🗑️ delete memory #{memory_command.memory_id}")
                else:
                    logger.info("⚠️ unprocess")
            except Exception as e:
                logger.error(f"❌ Error processing memory command {memory_command}: {e}, traceback is {traceback.format_exc()}")
        return {}
