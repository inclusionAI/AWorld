from typing import Any, Dict, List, Optional, Generic, TypeVar

from ...event import ContextEvent
from aworld.core.common import ActionResult
from aworld.memory.models import Fact

from aworld.logs.util import logger
from .langextract_op import LangExtractOp
from .op_factory import memory_op
from ... import ApplicationContext

# 定义泛型类型变量
T = TypeVar('T', bound=Fact)

@memory_op("extract_tool_fact")
class ExtractToolFactOp(LangExtractOp[Fact], Generic[T]):

    def __init__(self, name: str = "extract_tool_fact", **kwargs):
        """
        Initialize tool fact extraction operation
        
        Args:
            name: Operation name
            **kwargs: Additional configuration parameters
        """
        # Get prompt template and few-shot examples
        prompt = self._build_extraction_prompt_template()
        few_shots = self._get_few_shot_examples()
        
        # Call parent constructor
        super().__init__(
            name=name,
            prompt=prompt,
            extraction_classes=["tool_fact"],
            few_shots=few_shots,
            **kwargs
        )

    def _prepare_extraction_text(self, context: ApplicationContext, agent_id: str, event: ContextEvent = None) -> str:
        """
        Prepare text for fact extraction

        Args:
            context: Application context
            agent_id: Agent identifier
            event: Context event

        Returns:
            Formatted extraction text
        """
        if event and hasattr(event, 'tool_result') and event.tool_result:
            tool_result = event.tool_result
            if not isinstance(tool_result, ActionResult):

                return None
            return (f"- Tool Name: {tool_result.tool_name}\n"
                    f"- Action Name: {tool_result.action_name}\n"
                    f"- Parameters: {tool_result.parameter}\n"
                    f"- Result: {tool_result.content}\n")
        else:
            return f"\n\nConversation History:\n{context.get_history_desc()}"

    def _build_extraction_prompt_template(self) -> str:
        """Build extraction prompt template - focused on fact extraction from search results"""
        return """You are a search result fact analyzer, specialized in extracting key factual information relevant to the current task from web search tool results. Your main role is to identify valuable fact fragments from search results that will help agents better understand and execute related tasks.

Types of search result facts to extract:

1. **mathematical_data**: Mathematical calculations, statistical data, numerical information
2. **temporal_information**: Time, dates, historical events, timelines
3. **geographical_data**: Geographic locations, coordinates, map information, geographic features
4. **scientific_facts**: Scientific discoveries, research results, technical specifications, experimental data
5. **cultural_entertainment**: Movies, music, games, literary works, art information
6. **economic_financial**: Prices, stocks, economic indicators, financial data
7. **biological_medical**: Biological information, medical data, health-related facts
8. **technical_specifications**: Technical parameters, software versions, hardware specifications
9. **institutional_data**: Institutional information, organizational data, official records
10. **general_knowledge**: Common knowledge, definitions, explanations, background information

Please remember:
- Focus on extracting valuable factual information from search results
- Extract information directly helpful for current task execution
- Return empty list if search results contain no task-relevant information
- Ensure fact descriptions are clear, specific, and accurate
- Maintain objectivity and timeliness of facts
- Detect the language of user input and record facts in the same language
- Prioritize extracting specific factual details over general descriptions
- For current events, extract key elements like time, location, people
- For scientific knowledge, extract core content like data, principles, applications
- **Important: Entities must be atomic, each entity should be an independent, minimal information unit**
- **Do not merge multiple entities into one, e.g., "Apple, Google, Microsoft" should be extracted separately**
- **Each entity should contain a single concept, object, or fact, avoiding compound entities**

Format constraints:
- Do not add ```json or ``` markers before or after the output string
- Output string must be properly deserializable to a JSON object

User input
{{task_input}}
Todo list created based on user input
{{todo_info}}
"""

    def _get_few_shot_examples(self) -> List[Dict]:
        """Get few-shot examples for search result fact extraction"""
        return [
            {
                "type": "tool_fact",
                "input": "工具名称: web_search\n动作名称: search\n参数: {'search_term': '苹果公司2024年第三季度财报营收数据'}\n结果: 苹果公司2024年第三季度营收达到948.4亿美元，同比增长1.4%。iPhone营收为459.6亿美元，服务营收为212.1亿美元，Mac营收为74.0亿美元，iPad营收为57.9亿美元。净利润为236.4亿美元。",
                "output": [
                    {
                        "type": "ADD",
                        "item": {
                            "key": "mathematical_data",
                            "value": "苹果公司2024年第三季度营收948.4亿美元"
                        }
                    },
                    {
                        "type": "ADD",
                        "item": {
                            "key": "mathematical_data",
                            "value": "苹果公司2024年第三季度净利润236.4亿美元"
                        }
                    },
                    {
                        "type": "ADD",
                        "item": {
                            "key": "economic_financial",
                            "value": "iPhone营收459.6亿美元"
                        }
                    },
                    {
                        "type": "ADD",
                        "item": {
                            "key": "economic_financial",
                            "value": "服务营收212.1亿美元"
                        }
                    },
                    {
                        "type": "ADD",
                        "item": {
                            "key": "economic_financial",
                            "value": "Mac营收74.0亿美元"
                        }
                    },
                    {
                        "type": "ADD",
                        "item": {
                            "key": "economic_financial",
                            "value": "iPad营收57.9亿美元"
                        }
                    }
                ]
            },
            {
                "type": "tool_fact",
                "input": "工具名称: web_search\n动作名称: search\n参数: {'search_term': '詹姆斯韦伯太空望远镜最新发现JADES-GS-z14-0'}\n结果: 詹姆斯韦伯太空望远镜在2024年发现了迄今最遥远的星系JADES-GS-z14-0，距离地球约135亿光年，形成于宇宙大爆炸后仅2.9亿年。该星系质量约为太阳的几亿倍，亮度异常高。这一发现有助于理解早期宇宙的星系形成过程。",
                "output": [
                    {
                        "type": "ADD",
                        "item": {
                            "key": "scientific_facts",
                            "value": "发现迄今最遥远的星系JADES-GS-z14-0"
                        }
                    },
                    {
                        "type": "ADD",
                        "item": {
                            "key": "mathematical_data",
                            "value": "星系JADES-GS-z14-0距离地球约135亿光年"
                        }
                    },
                    {
                        "type": "ADD",
                        "item": {
                            "key": "temporal_information",
                            "value": "星系JADES-GS-z14-0形成于宇宙大爆炸后仅2.9亿年"
                        }
                    },
                    {
                        "type": "ADD",
                        "item": {
                            "key": "mathematical_data",
                            "value": "星系质量约为太阳的几亿倍"
                        }
                    },
                    {
                        "type": "ADD",
                        "item": {
                            "key": "scientific_facts",
                            "value": "星系亮度异常高"
                        }
                    }
                ]
            },
            {
                "type": "tool_fact",
                "input": "工具名称: web_search\n动作名称: search\n参数: {'search_term': '巴黎埃菲尔铁塔高度坐标位置'}\n结果: 埃菲尔铁塔位于法国巴黎，坐标为48.8584°N, 2.2945°E。铁塔高度为330米（包括天线），重约10,100吨。建于1889年，是巴黎的地标性建筑。",
                "output": [
                    {
                        "type": "ADD",
                        "item": {
                            "key": "geographical_data",
                            "value": "埃菲尔铁塔位于法国巴黎"
                        }
                    },
                    {
                        "type": "ADD",
                        "item": {
                            "key": "geographical_data",
                            "value": "埃菲尔铁塔坐标为48.8584°N, 2.2945°E"
                        }
                    },
                    {
                        "type": "ADD",
                        "item": {
                            "key": "mathematical_data",
                            "value": "埃菲尔铁塔高度为330米（包括天线）"
                        }
                    },
                    {
                        "type": "ADD",
                        "item": {
                            "key": "mathematical_data",
                            "value": "埃菲尔铁塔重约10,100吨"
                        }
                    },
                    {
                        "type": "ADD",
                        "item": {
                            "key": "temporal_information",
                            "value": "埃菲尔铁塔建于1889年"
                        }
                    },
                    {
                        "type": "ADD",
                        "item": {
                            "key": "general_knowledge",
                            "value": "埃菲尔铁塔是巴黎的地标性建筑"
                        }
                    }
                ]
            },
            {
                "type": "tool_fact",
                "input": "工具名称: web_search\n动作名称: search\n参数: {'search_term': 'Python 3.12新特性功能更新'}\n结果: Python 3.12于2023年10月2日发布，主要新特性包括：改进的错误消息、性能提升5-10%、新的f-string语法、类型系统增强、更好的Unicode支持。相比Python 3.11，启动时间减少约10%。",
                "output": [
                    {
                        "type": "ADD",
                        "item": {
                            "key": "temporal_information",
                            "value": "Python 3.12于2023年10月2日发布"
                        }
                    },
                    {
                        "type": "ADD",
                        "item": {
                            "key": "technical_specifications",
                            "value": "Python 3.12改进的错误消息"
                        }
                    },
                    {
                        "type": "ADD",
                        "item": {
                            "key": "technical_specifications",
                            "value": "Python 3.12新的f-string语法"
                        }
                    },
                    {
                        "type": "ADD",
                        "item": {
                            "key": "technical_specifications",
                            "value": "Python 3.12类型系统增强"
                        }
                    },
                    {
                        "type": "ADD",
                        "item": {
                            "key": "technical_specifications",
                            "value": "Python 3.12更好的Unicode支持"
                        }
                    },
                    {
                        "type": "ADD",
                        "item": {
                            "key": "mathematical_data",
                            "value": "Python 3.12性能提升5-10%"
                        }
                    },
                    {
                        "type": "ADD",
                        "item": {
                            "key": "mathematical_data",
                            "value": "Python 3.12启动时间减少约10%"
                        }
                    }
                ]
            },
            {
                "type": "tool_fact",
                "input": "工具名称: web_search\n动作名称: search\n参数: {'search_term': '人类基因组大小碱基对数量'}\n结果: 人类基因组包含约30亿个碱基对，分布在23对染色体上。基因组大小约为3.2GB，包含约20,000-25,000个基因。人类基因组计划于2003年完成，耗资约30亿美元。",
                "output": [
                    {
                        "type": "ADD",
                        "item": {
                            "key": "biological_medical",
                            "value": "人类基因组包含约30亿个碱基对"
                        }
                    },
                    {
                        "type": "ADD",
                        "item": {
                            "key": "biological_medical",
                            "value": "人类基因组分布在23对染色体上"
                        }
                    },
                    {
                        "type": "ADD",
                        "item": {
                            "key": "biological_medical",
                            "value": "人类基因组包含约20,000-25,000个基因"
                        }
                    },
                    {
                        "type": "ADD",
                        "item": {
                            "key": "mathematical_data",
                            "value": "人类基因组大小约为3.2GB"
                        }
                    },
                    {
                        "type": "ADD",
                        "item": {
                            "key": "mathematical_data",
                            "value": "人类基因组计划耗资约30亿美元"
                        }
                    },
                    {
                        "type": "ADD",
                        "item": {
                            "key": "temporal_information",
                            "value": "人类基因组计划于2003年完成"
                        }
                    }
                ]
            }
        ]

    def _build_memory_item(self, extract_data: Dict[str, Any], context: ApplicationContext, agent_id: str) -> Optional[Fact]:
        """
        Build Fact object from extracted data
        
        Args:
            extract_data: Extracted data
            context: Application context
            agent_id: Agent ID
            
        Returns:
            Fact object
        """
        try:
            if not extract_data or not isinstance(extract_data, dict):
                return None
                
            key = extract_data.get("key", "")
            value = extract_data.get("value", "")
            
            if not key or not value:
                return None
                
            return Fact(
                user_id=context.user_id,
                agent_id=agent_id,
                content={"key": key, "value": value},
                metadata={
                    "extraction_type": "tool_fact",
                    "confidence": 1.0,
                    "task_id": context.task_id,
                    "session_id": context.session_id,
                    "source": "tool_fact_extraction",
                }
            )
        except Exception as e:
            logger.error(f"❌ Error building fact from extract data: {e}")
            return None


# 创建默认实例
default_extract_tool_fact_op = ExtractToolFactOp()
