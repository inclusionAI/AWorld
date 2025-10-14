import os
import traceback
from abc import abstractmethod
from typing import Any, Dict, List, TypeVar, Generic, Optional

from aworld.logs.util import logger
from ... import ApplicationContext
from ...event import ContextEvent
from .base import BaseOp, MemoryCommand
from aworld.memory.models import MemoryItem
from aworldspace.prompt.prompt_ext import ContextPromptTemplate

try:
    # 尝试导入langextract，如果不可用则设为None
    import langextract as lx
    from langextract.factory import ModelConfig
    LANGEXTRACT_AVAILABLE = True
except ImportError:
    lx = None
    ModelConfig = None
    LANGEXTRACT_AVAILABLE = False

# 定义泛型类型变量
T = TypeVar('T', bound=MemoryItem)

class LangExtractOp(BaseOp, Generic[T]):
    """
    Abstract base class for langextract-based memory operations
    
    This class provides a framework for using langextract to extract structured information
    from text and convert it to memory commands. Subclasses should implement the specific
    extraction logic for their use case.
    """

    def __init__(self, name: str, prompt: str, extraction_classes: List[str], few_shots:list[Dict], **kwargs):
        """
        Initialize the LangExtractOp
        
        Args:
            name: Operation name
            prompt: Prompt template for extraction
            extraction_classes: List of extraction class names
            **kwargs: Additional configuration options
        """
        # 总是调用父类初始化
        super().__init__(name, **kwargs)
        
        # 如果langextract不可用，记录警告并设置默认值
        if not LANGEXTRACT_AVAILABLE:
            logger.warning("⚠️ langextract not available, skipping extraction")
            self.lx = None
            self.prompt_template = None
            self.few_shots = []
            self.extraction_classes = []
            self.top_k = 0
        else:
            self.lx = lx
            self.top_k = kwargs.get("top_k", 5)
            self.prompt_template = ContextPromptTemplate(template=prompt)
            self.few_shots = few_shots
            self.extraction_classes = extraction_classes

    async def execute(self, context: ApplicationContext, info: Dict[str, Any] = None, event: ContextEvent = None, **kwargs) -> Dict[str, Any]:
        """
        Execute the extraction operation
        
        Args:
            context: Application context
            info: Operation info dictionary
            event: Context event
            **kwargs: Additional arguments
            
        Returns:
            Updated info dictionary with memory commands
        """
        if not self.lx:
            logger.warning("⚠️ langextract not available, skipping extraction")
            return {}

        prompt = await self.prompt_template.async_format(context=context, agent_id=event.namespace)
        result = await self._extract_information(prompt, context, event.namespace, event)
        if not info:
            info = {}
        if info.get("memory_commands") is None:
            info["memory_commands"] = []
        info["memory_commands"].extend(result)
        return info

    async def _extract_information(self, prompt: str, context: ApplicationContext, agent_id: str, event: ContextEvent) -> List[MemoryCommand[T]]:
        """
        Extract information using langextract
        
        Args:
            prompt: Formatted prompt for extraction
            context: Application context
            agent_id: Agent identifier
            
        Returns:
            List of MemoryCommand objects
        """
        try:
            # 获取few-shot示例
            examples = self._prepare_examples()
            
            # 准备提取文本
            extraction_text = self._prepare_extraction_text(context, agent_id, event)
            if not extraction_text:
                return []
            
            # 运行提取
            from langextract.providers import openai
            from langextract import factory
            result = lx.extract(
                text_or_documents=extraction_text,
                prompt_description=prompt,
                examples=examples,
                # model_url=self.langextract_config['model_url'],
                # api_key=self.langextract_config['api_key'],
                # model_id=self.langextract_config["model_id"],
                # language_model_type=openai.OpenAILanguageModel,
                config=factory.ModelConfig(
                    model_id=os.environ['EXTRACT_MODEL_NAME'],
                    provider='openai',
                    provider_kwargs={"api_key": os.environ['EXTRACT_API_KEY'],
                                     "base_url": os.environ['EXTRACT_BASE_URL'],
                                     "provider": "openai"
                                     }
                ),
                fence_output=True, # Let it compute
                use_schema_constraints=False,
                max_workers=2
            )
            
            logger.info(f"✅ Successfully extracted information using langextract: {result}")
            
            # 处理提取结果并转换为 MemoryCommand 格式
            memory_commands = self._convert_extractions_to_memory_commands(result, context, agent_id)
            logger.info(f"🔄 Converted to {len(memory_commands)} memory commands")
            
            return memory_commands

        except Exception as e:
            logger.error(f"❌ Error during langextract extraction: {e} {traceback.format_exc()}")
            return []


    def _prepare_examples(self) -> List[Any]:
        """
        Prepare few-shot examples for user profile extraction

        Returns:
            List of examples in langextract format
        """
        try:
            examples = []
            for few_shot in self.few_shots:
                # 提取对话内容作为文本
                conversation_text = few_shot["input"]

                # 提取输出作为属性
                extractions = []
                for output_item in few_shot["output"]:
                    extraction = self.lx.data.Extraction(
                        extraction_class=few_shot["type"],
                        extraction_text=conversation_text,
                        attributes={
                            "type": output_item["type"],
                            "item": output_item["item"],
                            "memory_id": output_item.get("memory_id", None)
                        }
                    )
                    extractions.append(extraction)

                example = self.lx.data.ExampleData(
                    text=conversation_text,
                    extractions=extractions
                )
                examples.append(example)

            return examples

        except Exception as err:
            logger.warning(f"⚠️ {type(self)} _prepare_examples failed, err is {err}")
            return []

    @abstractmethod
    def _prepare_extraction_text(self, context: ApplicationContext, agent_id: str, event: ContextEvent) -> str:
        """
        Prepare the text to be processed by langextract
        
        Args:
            context: Application context
            agent_id: Agent identifier
            
        Returns:
            Formatted text for extraction
        """
        pass

    def _convert_extractions_to_memory_commands(self, extractions_result: Any, context: ApplicationContext,
                                                agent_id: str) -> List[MemoryCommand[T]]:
        """
        Convert langextract extraction results to MemoryCommand format for user profiles

        Args:
            extractions_result: Result from langextract.extract()
            context: Application context
            agent_id: Agent identifier

        Returns:
            List of MemoryCommand objects for user profile updates
        """
        memory_commands = []

        try:
            if not extractions_result.extractions:
                return []

            # 处理提取结果
            for extraction in extractions_result.extractions:
                if extraction.extraction_class in self.extraction_classes:
                    # 提取属性
                    attributes = extraction.attributes
                    operation_type = attributes.get("type")
                    extract_data = attributes.get("item")
                    memory_id = attributes.get("memory_id")

                    memory_item = self._build_memory_item(extract_data, context, agent_id)
                    if not memory_item:
                        continue
                    # 创建 MemoryCommand
                    if operation_type == "ADD":
                        command = MemoryCommand(
                            type="ADD",
                            item=memory_item,
                            memory_id=None
                        )
                    elif operation_type == "DELETE":
                        command = MemoryCommand(
                            type="DELETE",
                            item=memory_item,
                            memory_id=memory_id
                        )
                    elif operation_type == "KEEP":
                        command = MemoryCommand(
                            type="KEEP",
                            item=memory_item,
                            memory_id=memory_id
                        )
                    else:
                        logger.warning(f"⚠️ Unknown operation type: {operation_type}")
                        continue

                    memory_commands.append(command)
        except Exception as e:
            logger.error(f"❌ Error converting extractions to memory commands: {e} {traceback.format_exc()}")

        return memory_commands

    def _get_model_config(self):
        """
        Get the model configuration for langextract
        
        Returns:
            ModelConfig object or None if langextract is not available
        """
        if not LANGEXTRACT_AVAILABLE or ModelConfig is None:
            return None
        
        # 获取模型名称，如果没有设置则使用默认值
        model_id = os.environ.get("LLM_MODEL_NAME", "gpt-4o")
        
        # 准备提供者参数
        provider_kwargs = {}
        
        # 添加 API 密钥
        api_key = os.environ.get('LLM_API_KEY')
        if api_key:
            provider_kwargs["api_key"] = api_key
        
        # 添加基础 URL
        base_url = os.environ.get("LLM_BASE_URL")
        if base_url:
            provider_kwargs["base_url"] = base_url
        
        # 不指定 provider，让 langextract 根据 model_id 自动解析
        # 这样可以避免 "No provider found matching: 'openai'" 错误
        return ModelConfig(
            model_id=model_id,
            provider='openai',  # 让 langextract 自动解析提供者
            provider_kwargs=provider_kwargs
        )

    def _build_memory_item(self, extract_data: Dict[str, Any], context: ApplicationContext, agent_id: str) -> Optional[T]:
        pass


