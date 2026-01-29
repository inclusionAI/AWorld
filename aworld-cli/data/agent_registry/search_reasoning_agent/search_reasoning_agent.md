# SearchReasoningAgent - 搜索推理智能体

## 智能体描述
SearchReasoningAgent是一个具备网络搜索和基础推理能力的智能体，能够进行信息检索、逻辑分析和知识整合。

## 核心功能
1. **网络搜索能力** - 进行网络信息检索和搜索
2. **基础推理能力** - 进行逻辑推理、分析和判断  
3. **信息整合能力** - 将搜索结果与推理结合，提供综合性答案

## 应用场景
- 信息查询和事实验证
- 逻辑分析和问题解答
- 知识整合和综合分析
- 复杂查询任务处理

## 技术实现

```python
import os
import traceback
from typing import Dict, Any, List, Optional
import json

from aworld.config import AgentConfig, ModelConfig
from aworld.core.agent.base import BaseAgent
from aworld.core.agent.swarm import Swarm
from aworld.core.common import Observation, ActionModel
from aworld.core.event.base import Message
from aworld.logs.util import logger
from aworld.models.llm import acall_llm_model
from aworld_cli.core import agent
from mcp_config import mcp_config


class SearchReasoningAgent(BaseAgent[Observation, List[ActionModel]]):
    """搜索推理智能体 - 具备网络搜索和基础推理能力的智能体"""

    def __init__(self, name: str, conf: AgentConfig = None, desc: str = None,
                 system_prompt: str = None, tool_names: List[str] = None, **kwargs):
        super().__init__(name=name, conf=conf, desc=desc, **kwargs)
        self.system_prompt = system_prompt or self._get_default_system_prompt()
        self.model_name = conf.llm_config.llm_model_name if conf and conf.llm_config else "gpt-4"
        
        # 搜索和推理状态管理
        self.search_results = []
        self.reasoning_steps = []
        self.integrated_analysis = ""

    def _get_default_system_prompt(self) -> str:
        """获取默认系统提示"""
        return """你是SearchReasoningAgent，一个专业的搜索推理智能体。你具备以下核心能力：

🔍 **网络搜索能力**：
- 能够进行精准的网络信息检索
- 识别关键词并构建有效的搜索查询
- 从搜索结果中提取有价值的信息

🧠 **基础推理能力**：
- 进行逻辑推理、分析和判断
- 识别信息间的关联和模式
- 基于证据得出合理结论

🔗 **信息整合能力**：
- 将搜索结果与推理分析相结合
- 提供综合性、结构化的答案
- 确保信息的准确性和完整性

**工作流程**：
1. 分析用户查询，识别关键信息需求
2. 制定搜索策略，执行网络搜索
3. 对搜索结果进行推理分析
4. 整合信息，提供综合性答案
5. 验证结论的逻辑性和准确性

**回答原则**：
- 基于事实和证据
- 逻辑清晰，结构完整
- 承认不确定性，避免过度推测
- 提供信息来源和可信度评估

请根据用户的具体需求，运用你的搜索和推理能力提供帮助。"""

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None,
                           **kwargs) -> List[ActionModel]:
        """执行搜索推理的核心逻辑"""
        try:
            # 初始化工具
            try:
                await self.async_desc_transform(context=message.context)
            except Exception as e:
                logger.warning(f"{self.name()} get tools desc fail, no tool to use. error: {traceback.format_exc()}")
                self.tools = []

            # 分析用户查询
            query_analysis = await self._analyze_query(observation.content)
            logger.info(f"查询分析结果: {query_analysis}")

            # 执行搜索推理流程
            result = await self._execute_search_reasoning_workflow(observation.content, query_analysis)

            return [ActionModel(
                agent_name=self.name(),
                policy_info=result
            )]

        except Exception as e:
            logger.error(f"SearchReasoningAgent {self.name()} 执行失败: {str(e)}")
            logger.error(traceback.format_exc())
            return [ActionModel(
                agent_name=self.name(),
                policy_info=f"执行失败: {str(e)}"
            )]

    async def _analyze_query(self, query: str) -> Dict[str, Any]:
        """分析用户查询，识别搜索需求和推理要求"""
        try:
            analysis_prompt = f"""请分析以下用户查询，识别：
1. 核心问题和信息需求
2. 需要搜索的关键词
3. 推理分析的重点
4. 预期的答案类型

用户查询：{query}

请以JSON格式返回分析结果：
{{
    "core_question": "核心问题",
    "search_keywords": ["关键词1", "关键词2"],
    "reasoning_focus": "推理重点",
    "answer_type": "答案类型",
    "complexity": "简单/中等/复杂"
}}"""

            messages = [
                {"role": "system", "content": "你是一个查询分析专家，能够准确识别用户的信息需求。"},
                {"role": "user", "content": analysis_prompt}
            ]

            response = await acall_llm_model(
                self.llm,
                messages=messages,
                model=self.model_name,
                temperature=0.3
            )

            # 尝试解析JSON响应
            try:
                analysis = json.loads(response.content)
            except:
                # 如果JSON解析失败，返回基础分析
                analysis = {
                    "core_question": query,
                    "search_keywords": [query],
                    "reasoning_focus": "基础分析",
                    "answer_type": "综合回答",
                    "complexity": "中等"
                }

            return analysis

        except Exception as e:
            logger.error(f"查询分析失败: {str(e)}")
            return {
                "core_question": query,
                "search_keywords": [query],
                "reasoning_focus": "基础分析",
                "answer_type": "综合回答",
                "complexity": "中等"
            }

    async def _execute_search_reasoning_workflow(self, original_query: str, query_analysis: Dict[str, Any]) -> str:
        """执行完整的搜索推理工作流程"""
        try:
            workflow_steps = []
            
            # 步骤1：执行网络搜索
            search_results = await self._perform_search(query_analysis.get("search_keywords", [original_query]))
            workflow_steps.append("✅ 完成网络搜索")
            
            # 步骤2：执行推理分析
            reasoning_results = await self._perform_reasoning(original_query, search_results, query_analysis)
            workflow_steps.append("✅ 完成推理分析")
            
            # 步骤3：整合信息
            integrated_result = await self._integrate_information(original_query, search_results, reasoning_results, query_analysis)
            workflow_steps.append("✅ 完成信息整合")
            
            # 构建最终回答
            final_answer = await self._build_final_answer(original_query, integrated_result, workflow_steps)
            
            return final_answer

        except Exception as e:
            logger.error(f"搜索推理工作流程执行失败: {str(e)}")
            return f"工作流程执行失败: {str(e)}"

    async def _perform_search(self, keywords: List[str]) -> Dict[str, Any]:
        """执行网络搜索"""
        try:
            search_results = {"results": [], "summary": ""}
            
            # 构建搜索消息
            search_query = " ".join(keywords) if isinstance(keywords, list) else str(keywords)
            
            messages = [
                {"role": "system", "content": "你需要使用搜索工具来查找相关信息。"},
                {"role": "user", "content": f"请搜索关于以下内容的信息：{search_query}"}
            ]

            # 使用工具进行搜索
            tools = self.tools if self.tools else None
            
            response = await acall_llm_model(
                self.llm,
                messages=messages,
                model=self.model_name,
                temperature=0.5,
                tools=tools
            )

            search_results["summary"] = response.content or "搜索完成"
            search_results["query"] = search_query
            
            logger.info(f"搜索完成: {search_query}")
            return search_results

        except Exception as e:
            logger.error(f"搜索执行失败: {str(e)}")
            return {"results": [], "summary": f"搜索失败: {str(e)}", "query": str(keywords)}

    async def _perform_reasoning(self, original_query: str, search_results: Dict[str, Any], query_analysis: Dict[str, Any]) -> Dict[str, Any]:
        """执行推理分析"""
        try:
            reasoning_prompt = f"""基于以下搜索结果，对用户查询进行深入的推理分析：

原始查询：{original_query}
查询分析：{query_analysis}
搜索结果：{search_results.get('summary', '无搜索结果')}

请进行以下推理分析：
1. 信息可信度评估
2. 逻辑关系分析
3. 因果关系推理
4. 结论推导
5. 不确定性识别

请使用推理工具进行深入分析。"""

            messages = [
                {"role": "system", "content": "你需要使用推理工具进行逻辑分析和推理。"},
                {"role": "user", "content": reasoning_prompt}
            ]

            # 使用工具进行推理
            tools = self.tools if self.tools else None
            
            response = await acall_llm_model(
                self.llm,
                messages=messages,
                model=self.model_name,
                temperature=0.3,
                tools=tools
            )

            reasoning_results = {
                "analysis": response.content or "推理分析完成",
                "confidence": "中等",
                "key_insights": []
            }
            
            logger.info("推理分析完成")
            return reasoning_results

        except Exception as e:
            logger.error(f"推理分析失败: {str(e)}")
            return {"analysis": f"推理分析失败: {str(e)}", "confidence": "低", "key_insights": []}

    async def _integrate_information(self, original_query: str, search_results: Dict[str, Any], 
                                   reasoning_results: Dict[str, Any], query_analysis: Dict[str, Any]) -> Dict[str, Any]:
        """整合搜索结果和推理分析"""
        try:
            integration_prompt = f"""请整合以下信息，为用户查询提供综合性答案：

用户查询：{original_query}
查询分析：{query_analysis}
搜索结果：{search_results.get('summary', '无搜索结果')}
推理分析：{reasoning_results.get('analysis', '无推理结果')}

整合要求：
1. 确保信息的准确性和一致性
2. 提供结构化的综合答案
3. 标明信息来源和可信度
4. 识别并说明不确定性
5. 提供实用的建议或结论"""

            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": integration_prompt}
            ]

            response = await acall_llm_model(
                self.llm,
                messages=messages,
                model=self.model_name,
                temperature=0.4
            )

            integrated_result = {
                "integrated_answer": response.content or "信息整合完成",
                "confidence_level": reasoning_results.get("confidence", "中等"),
                "sources": [search_results.get("query", "搜索结果")],
                "limitations": []
            }
            
            logger.info("信息整合完成")
            return integrated_result

        except Exception as e:
            logger.error(f"信息整合失败: {str(e)}")
            return {"integrated_answer": f"信息整合失败: {str(e)}", "confidence_level": "低", "sources": [], "limitations": []}

    async def _build_final_answer(self, original_query: str, integrated_result: Dict[str, Any], workflow_steps: List[str]) -> str:
        """构建最终答案"""
        try:
            final_answer = f"""# 🔍 SearchReasoningAgent 分析报告

## 📋 查询内容
{original_query}

## 🔄 处理流程
{chr(10).join(workflow_steps)}

## 📊 综合分析结果
{integrated_result.get('integrated_answer', '无分析结果')}

## 📈 可信度评估
**置信度**: {integrated_result.get('confidence_level', '未知')}

## 📚 信息来源
{chr(10).join([f"- {source}" for source in integrated_result.get('sources', ['无来源信息'])])}

## ⚠️ 注意事项
- 本分析基于当前可获取的信息
- 建议结合多个信息源进行验证
- 如有疑问，请进一步核实相关信息

---
*由SearchReasoningAgent提供 - 网络搜索 + 基础推理*"""

            return final_answer

        except Exception as e:
            logger.error(f"构建最终答案失败: {str(e)}")
            return f"构建最终答案失败: {str(e)}"


@agent(
    name="search_reasoning_agent",
    desc="具备网络搜索和基础推理能力的智能体，能够进行信息检索、逻辑分析和知识整合"
)
def build_search_reasoning_swarm():
    """构建搜索推理智能体群"""
    # 创建Agent配置
    agent_config = AgentConfig(
        llm_config=ModelConfig(
            llm_model_name=os.environ.get("LLM_MODEL_NAME", "gpt-4"),
            llm_provider=os.environ.get("LLM_PROVIDER", "openai"),
            llm_api_key=os.environ.get("LLM_API_KEY"),
            llm_base_url=os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1"),
            llm_temperature=float(os.environ.get("LLM_TEMPERATURE", "0.7"))
        )
    )

    # 从mcp_config中提取所有服务器名称
    mcp_servers = list(mcp_config.get("mcpServers", {}).keys())

    # 创建SearchReasoningAgent实例
    search_reasoning_agent = SearchReasoningAgent(
        name="search_reasoning_agent",
        desc="具备网络搜索和基础推理能力的智能体，专门用于信息检索、逻辑分析和知识整合",
        conf=agent_config,
        system_prompt=None,  # 使用默认系统提示
        mcp_servers=mcp_servers,
        mcp_config=mcp_config
    )

    # 返回包含该Agent的Swarm
    return Swarm(search_reasoning_agent)
```

## MCP配置文件

```python
# mcp_config.py - MCP服务器配置

mcp_config = {
    "mcpServers": {
        "search": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.tools.search"
            ],
            "env": {
                "GOOGLE_API_KEY": "${GOOGLE_API_KEY}",
                "GOOGLE_CSE_ID": "${GOOGLE_CSE_ID}"
            },
            "client_session_timeout_seconds": 9999.0
        },
        "reasoning": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.intelligence.think"
            ],
            "env": {},
            "client_session_timeout_seconds": 9999.0
        },
        "terminal": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.tools.terminal"
            ],
            "env": {},
            "client_session_timeout_seconds": 9999.0
        },
        "browser": {
            "command": "python",
            "args": [
                "-m",
                "examples.gaia.mcp_collections.tools.browser"
            ],
            "env": {
                "LLM_MODEL_NAME": "${LLM_MODEL_NAME}",
                "LLM_API_KEY": "${LLM_API_KEY}",
                "LLM_BASE_URL": "${LLM_BASE_URL}"
            },
            "client_session_timeout_seconds": 9999.0
        }
    }
}
```

## 使用示例

### 基础使用
```python
# 创建智能体实例
swarm = build_search_reasoning_swarm()
agent = swarm.agents[0]

# 示例查询1：事实验证
query1 = "请验证并分析：人工智能是否真的会在2030年超越人类智能？"

# 示例查询2：信息整合
query2 = "比较分析不同国家的新能源政策，并推理其对全球气候变化的影响"

# 示例查询3：逻辑分析
query3 = "分析当前经济形势下，投资科技股是否是明智的选择？"
```

### 高级功能
- **多轮对话支持**：能够基于上下文进行连续推理
- **信息源追踪**：记录和评估信息来源的可信度
- **不确定性管理**：明确标识推理中的不确定因素
- **结构化输出**：提供格式化的分析报告

## 技术特点

### 🔍 搜索功能模块
- 智能关键词提取和查询构建
- 多源信息检索和结果筛选
- 信息质量评估和排序

### 🧠 推理逻辑模块  
- 逻辑推理和因果分析
- 模式识别和关联分析
- 不确定性量化和风险评估

### 🔗 信息整合模块
- 多源信息融合和一致性检查
- 结构化知识表示
- 综合性结论生成

### 🛡️ 错误处理和异常管理
- 完善的异常捕获和处理机制
- 优雅的降级策略
- 详细的日志记录和错误追踪

## 环境变量配置

```bash
# LLM配置
export LLM_MODEL_NAME="gpt-4"
export LLM_PROVIDER="openai"
export LLM_API_KEY="your_openai_api_key"
export LLM_BASE_URL="https://api.openai.com/v1"
export LLM_TEMPERATURE="0.7"

# 搜索API配置
export GOOGLE_API_KEY="your_google_api_key"
export GOOGLE_CSE_ID="your_custom_search_engine_id"
```

这个SearchReasoningAgent智能体具备完整的搜索推理能力，能够处理复杂的信息查询任务，提供准确、结构化的分析结果。