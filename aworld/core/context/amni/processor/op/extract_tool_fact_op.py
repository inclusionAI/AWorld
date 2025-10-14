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
        初始化工具事实提取操作
        
        Args:
            name: 操作名称
            **kwargs: 额外配置参数
        """
        # 获取提示模板和few-shot示例
        prompt = self._build_extraction_prompt_template()
        few_shots = self._get_few_shot_examples()
        
        # 调用父类构造函数
        super().__init__(
            name=name,
            prompt=prompt,
            extraction_classes=["tool_fact"],
            few_shots=few_shots,
            **kwargs
        )

    def _prepare_extraction_text(self, context: ApplicationContext, agent_id: str, event: ContextEvent = None) -> str:
        """
        准备用于事实提取的文本

        Args:
            context: 应用上下文
            agent_id: 代理标识符
            event: 上下文事件

        Returns:
            格式化的提取文本
        """
        if event and hasattr(event, 'tool_result') and event.tool_result:
            tool_result = event.tool_result
            if not isinstance(tool_result, ActionResult):

                return None
            return (f"- 工具名称: {tool_result.tool_name}\n"
                    f"- 动作名称: {tool_result.action_name}\n"
                    f"- 参数: {tool_result.parameter}\n"
                    f"- 结果: {tool_result.content}\n")
        else:
            return f"\n\n对话历史:\n{context.get_history_desc()}"

    def _build_extraction_prompt_template(self) -> str:
        """构建提取提示模板 - 专注于搜索结果的事实提取"""
        return """你是一个搜索结果事实分析器，专门从web搜索工具的结果中提取与当前任务相关的关键事实信息。你的主要作用是从搜索结果中识别有价值的事实片段，这些事实将帮助agent更好地理解和执行相关任务。

需要提取的搜索结果事实类型：

1. **mathematical_data**：数学计算、统计数据、数值信息
2. **temporal_information**：时间、日期、历史事件、时间线
3. **geographical_data**：地理位置、坐标、地图信息、地理特征
4. **scientific_facts**：科学发现、研究结果、技术规格、实验数据
5. **cultural_entertainment**：电影、音乐、游戏、文学作品、艺术信息
6. **economic_financial**：价格、股票、经济指标、金融数据
7. **biological_medical**：生物信息、医学数据、健康相关事实
8. **technical_specifications**：技术参数、软件版本、硬件规格
9. **institutional_data**：机构信息、组织数据、官方记录
10. **general_knowledge**：常识、定义、解释、背景信息

请记住以下几点：
- 专注于从搜索结果中提取有价值的事实信息
- 提取对当前任务执行有直接帮助的信息
- 如果搜索结果中没有任务相关信息，返回空列表
- 确保事实描述清晰、具体、准确
- 保持事实的客观性和时效性
- 你应该检测用户输入的语言，并用相同的语言记录事实
- 优先提取具体的事实细节而非泛泛的描述
- 对于时事信息，注意提取时间、地点、人物等关键要素
- 对于科学知识，注意提取数据、原理、应用等核心内容
- **重要：实体必须原子化，每个实体应该是独立的、最小的信息单元**
- **禁止将多个实体合并为一个实体，如"东方财富、广东甘化、红星发展"应分别提取**
- **每个实体应该包含单一的概念、对象或事实，避免复合实体**

格式约束：
- 注意输出字符串前后不要加任何 ```json 或 ``` 这样的标记
- 输出字符串必须可以被正常反序列化为json对象

用户输入
{{task_input}}
根据用户输入制订出的待办列表
{{todo_info}}
"""

    def _get_few_shot_examples(self) -> List[Dict]:
        """获取搜索结果事实提取的few-shot示例"""
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
        从提取数据构建Fact对象
        
        Args:
            extract_data: 提取的数据
            context: 应用上下文
            agent_id: 代理ID
            
        Returns:
            Fact对象
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
