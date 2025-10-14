AMNI_CONTEXT_PROMPT = {}

AMNI_CONTEXT_PROMPT["SUMMARY_CONVERSATION"] = """
你是一个专业的对话总结助手。你的任务是对用户与AI之间的对话历史进行关键信息总结，生成简洁的摘要，以便后续对话中能够快速召回相关内容。

## 对话内容：

{conversation_content}

## 总结要求：

### 1. 核心信息提取
- 识别对话的主要主题
- 提取关键决策、计划或结果
- 记录重要的具体信息（时间、地点、数量等）
- 总结用户的核心需求

### 2. 简洁明了
- 只保留最重要的信息
- 使用清晰简洁的语言
- 避免冗余和重复内容

### 3. 可召回性
- 包含用户可能用来查询的关键词
- 记录对话的核心成果

## 输出格式：

请按照以下JSON格式输出总结：

```json
{{
  "summary_content": "一段简洁的总结描述，包含对话的主要主题、关键成果、重要决策和搜索关键词等信息"
}}
```

## 示例场景：

### 1. 旅游计划讨论
如果用户与AI讨论了一个旅游计划，总结应该包含：
- 目的地、时间等关键信息
- 已确定的行程安排
- 相关的搜索关键词（如"旅游计划"、"上次的旅行"等）

### 2. 工作项目规划
如果用户与AI讨论了一个工作项目，总结应该包含：
- 项目名称、截止日期等关键信息
- 已确定的项目计划和分工
- 相关的搜索关键词（如"工作项目"、"上次的项目"等）

### 3. 学习计划制定
如果用户与AI讨论了一个学习计划，总结应该包含：
- 学习目标、时间安排等关键信息
- 已确定的学习内容和进度
- 相关的搜索关键词（如"学习计划"、"上次的学习"等）

### 4. 购物决策讨论
如果用户与AI讨论了购物决策，总结应该包含：
- 商品类型、预算范围等关键信息
- 已确定的购买选择
- 相关的搜索关键词（如"购物计划"、"上次的购买"等）

### 5. 健康管理计划
如果用户与AI讨论了健康管理，总结应该包含：
- 健康目标、时间安排等关键信息
- 已确定的健康计划
- 相关的搜索关键词（如"健康计划"、"上次的健康管理"等）

这样当用户后续说"我上次的那个XX"时，系统就能快速定位到相关信息。

请根据对话内容生成相应的总结。
"""


AMNI_CONTEXT_PROMPT["AI_CONTEXT_PART"] = """

<relevant_conversation_history>
{relevant_conversation_history}
</relevant_conversation_history>

<knowledge_context>
{knowledge_context}
</knowledge_context>
"""


AMNI_CONTEXT_PROMPT["KNOWLEDGE_PART"] = """
<knowledge_chunks_index>
{knowledge_index}
</knowledge_chunks_index>

<knowledge_relevant_chunks>
{knowledge_chunks}
</knowledge_relevant_chunks>

<knowledge_tools_tip>
you can read knowledge index and use the below tools to retrival more information:
1. get_knowledge(knowledge_id): obtain full content of knowledge, only return 2000 characters
2. get_knowledge_chunk(knowledge_id, chunk_index): obtain special chunk content of knowledge, start from 0
2. search_knowledge_chunks(query): Search knowledge by semantic query in the workspace
</knowledge_tools_tip>
"""


AMNI_CONTEXT_PROMPT["SUMMARY_PROMPT"] = """
You are a helpful assistant that summarizes the conversation history.
- Summarize the following text in one clear and concise paragraph, capturing the key ideas without missing critical points. 
- Ensure the summary is easy to understand and avoids excessive detail.

Here are the content: 
{context}
"""

USER_PROFILE_FEW_SHOTS = [
    {
        "type":"user_profile",
        "input": ("Conversation: [{'role': 'user', 'content': 'I am a 28-year-old software developer living in San Francisco. I love clean code and prefer Python over JavaScript.'}]"
                 "ExistedItems: []"),
        "output": [
            {
                "type": "ADD",
                "item": {
                    "key": "personal.basic",
                    "value": {
                        "age": "28",
                        "occupation": "software developer",
                        "location": "San Francisco"
                    }
                }
            },
            {
                "type": "ADD",
                "item": {
                    "key": "preferences.technical",
                    "value": {
                        "coding_style": "clean code",
                        "preferred_languages": ["Python"],
                        "less_preferred_languages": ["JavaScript"]
                    }
                }
            }
        ]
    },
    {
        "type":"user_profile",
        "input": ("Conversation: [{'role': 'user', 'content': 'Actually, I just moved to New York last month and I\'m now working as a data scientist. I still love Python though.'}]"
                 "ExistedItems: [{'memory_id': 'mem_001', 'key': 'personal.basic', 'value': {'age': '28', 'occupation': 'software developer', 'location': 'San Francisco'}}, {'memory_id': 'mem_002', 'key': 'preferences.technical', 'value': {'coding_style': 'clean code', 'preferred_languages': ['Python'], 'less_preferred_languages': ['JavaScript']}}]"),
        "output": [
            {
                "type": "DELETE",
                "memory_id": "mem_001",
                "item": {
                    "key": "personal.basic",
                    "value": {
                        "age": "28",
                        "occupation": "software developer",
                        "location": "San Francisco"
                    }
                }
            },
            {
                "type": "ADD",
                "item": {
                    "key": "personal.basic",
                    "value": {
                        "age": "28",
                        "occupation": "data scientist",
                        "location": "New York",
                        "relocation_date": "last month"
                    }
                }
            },
            {
                "type": "KEEP",
                "item": {
                    "key": "preferences.technical",
                    "value": {
                        "coding_style": "clean code",
                        "preferred_languages": ["Python"],
                        "less_preferred_languages": ["JavaScript"]
                    }
                }
            }
        ]
    },
    {
        "type":"user_profile",
        "input": ("Conversation: [{'role': 'user', 'content': 'I\'m learning machine learning and I\'ve started using TensorFlow. Also, I prefer working from home now.'}]"
                 "ExistedItems: [{'memory_id': 'mem_003', 'key': 'personal.basic', 'value': {'age': '28', 'occupation': 'data scientist', 'location': 'New York', 'relocation_date': 'last month'}}, {'memory_id': 'mem_002', 'key': 'preferences.technical', 'value': {'coding_style': 'clean code', 'preferred_languages': ['Python'], 'less_preferred_languages': ['JavaScript']}}]"),
        "output": [
            {
                "type": "KEEP",
                "item": {
                    "key": "personal.basic",
                    "value": {
                        "age": "28",
                        "occupation": "data scientist",
                        "location": "New York",
                        "relocation_date": "last month"
                    }
                }
            },
            {
                "type": "ADD",
                "item": {
                    "key": "skills.technical",
                    "value": {
                        "learning": ["machine learning"],
                        "tools": ["TensorFlow"]
                    }
                }
            },
            {
                "type": "ADD",
                "item": {
                    "key": "preferences.work",
                    "value": {
                        "work_location": "home",
                        "work_style": "remote"
                    }
                }
            },
            {
                "type": "KEEP",
                "item": {
                    "key": "preferences.technical",
                    "value": {
                        "coding_style": "clean code",
                        "preferred_languages": ["Python"],
                        "less_preferred_languages": ["JavaScript"]
                    }
                }
            }
        ]
    },
    {
        "type":"user_profile",
        "input": ("Conversation: [{'role': 'user', 'content': 'I changed my mind about JavaScript. I actually find it quite useful for web development now.'}]"
                 "ExistedItems: [{'memory_id': 'mem_002', 'key': 'preferences.technical', 'value': {'coding_style': 'clean code', 'preferred_languages': ['Python'], 'less_preferred_languages': ['JavaScript']}}]"),
        "output": [
            {
                "type": "DELETE",
                "memory_id": "mem_002",
                "item": {
                    "key": "preferences.technical",
                    "value": {
                        "coding_style": "clean code",
                        "preferred_languages": ["Python"],
                        "less_preferred_languages": ["JavaScript"]
                    }
                }
            },
            {
                "type": "ADD",
                "item": {
                    "key": "preferences.technical",
                    "value": {
                        "coding_style": "clean code",
                        "preferred_languages": ["Python", "JavaScript"],
                        "less_preferred_languages": []
                    }
                }
            }
        ]
    },
    {
        "type":"user_profile",
        "input": ("Conversation: [{'role': 'user', 'content': 'I\'m not really into gaming anymore. I prefer reading books and hiking.'}]"
                 "ExistedItems: [{'memory_id': 'mem_004', 'key': 'preferences.lifestyle', 'value': {'hobbies': ['gaming', 'reading'], 'activities': ['hiking']}}]"),
        "output": [
            {
                "type": "DELETE",
                "memory_id": "mem_004",
                "item": {
                    "key": "preferences.lifestyle",
                    "value": {
                        "hobbies": ["gaming", "reading"],
                        "activities": ["hiking"]
                    }
                }
            },
            {
                "type": "ADD",
                "item": {
                    "key": "preferences.lifestyle",
                    "value": {
                        "hobbies": ["reading"],
                        "activities": ["hiking"],
                        "dislikes": ["gaming"]
                    }
                }
            }
        ]
    }
]

AGENT_EXPERIENCE_FEW_SHOTS = [
    {
        "type": "agent_experience",
        "input": ("Conversation: [{'role': 'user', 'content': '用户需要创建一个Python项目并运行代码。Agent首先创建了项目目录，然后生成了main.py文件，最后在终端中运行了python main.py命令。'}]"
                 "ExistedItems: []"),
        "output": [
            {
                "type": "ADD",
                "item": {
                    "skill": "project_setup_and_execution",
                    "actions": [
                        "使用 mkdir 命令创建项目目录 /home/project/src，设置目录权限为755",
                        "使用 edit_file 工具创建 main.py 文件，路径为 /home/project/src/main.py，内容包含完整的Python代码",
                        "使用 run_terminal_cmd 执行 'cd /home/project/src && python main.py' 命令，验证代码运行结果"
                    ]
                }
            }
        ]
    },
    {
        "type": "agent_experience",
        "input": ("Conversation: [{'role': 'user', 'content': 'Agent需要调用OpenAI API但遇到了速率限制。Agent检查了错误响应头中的retry-after值，实现了指数退避重试机制，最终成功完成了API调用。'}]"
                 "ExistedItems: [{'memory_id': 'exp_001', 'skill': 'api_error_handling', 'actions': ['使用 call_api 调用API', '处理错误响应']}]"),
        "output": [
            {
                "type": "DELETE",
                "memory_id": "exp_001",
                "item": {
                    "skill": "api_error_handling",
                    "actions": ["使用 call_api 调用API", "处理错误响应"]
                }
            },
            {
                "type": "ADD",
                "item": {
                    "skill": "api_error_handling",
                    "actions": [
                        "使用 call_api 调用OpenAI API，设置请求头包含Authorization和Content-Type",
                        "解析API错误响应，提取retry-after头字段值，计算下次重试时间",
                        "实现指数退避算法，使用 sleep(retry_delay * 2^n) 进行延迟，n为重试次数",
                        "重新调用API直到成功，记录重试次数和最终结果状态"
                    ]
                }
            }
        ]
    },
    {
        "type": "agent_experience",
        "input": ("Conversation: [{'role': 'user', 'content': '用户提供了CSV数据文件，Agent使用pandas读取数据，分析了数据统计信息，创建了可视化图表，并输出了分析报告。'}]"
                 "ExistedItems: [{'memory_id': 'exp_002', 'skill': 'file_operations', 'actions': ['使用 read_file 读取文件', '使用 edit_file 编辑文件']}]"),
        "output": [
            {
                "type": "KEEP",
                "item": {
                    "skill": "file_operations",
                    "actions": ["使用 read_file 读取文件", "使用 edit_file 编辑文件"]
                }
            },
            {
                "type": "ADD",
                "item": {
                    "skill": "data_analysis_and_visualization",
                    "actions": [
                        "使用 read_file 读取CSV文件，指定编码为utf-8，处理可能的编码错误",
                        "使用 pandas.read_csv() 加载数据，设置参数 encoding='utf-8', delimiter=',', header=0",
                        "执行 data.describe() 获取统计摘要，使用 data.isnull().sum() 检查缺失值",
                        "使用 matplotlib.pyplot 创建图表，设置图表类型为柱状图，配置标题和标签",
                        "使用 savefig() 保存图表为PNG格式，设置DPI为300，输出到output目录"
                    ]
                }
            }
        ]
    },
    {
        "type": "agent_experience",
        "input": ("Conversation: [{'role': 'user', 'content': 'Agent需要帮助用户调试一个React组件的问题。Agent首先检查了代码语法，然后使用浏览器开发者工具分析DOM结构，最后提供了修复建议和优化代码。'}]"
                 "ExistedItems: [{'memory_id': 'exp_003', 'skill': 'code_debugging', 'actions': ['检查代码语法', '分析错误信息']}]"),
        "output": [
            {
                "type": "DELETE",
                "memory_id": "exp_003",
                "item": {
                    "skill": "code_debugging",
                    "actions": ["检查代码语法", "分析错误信息"]
                }
            },
            {
                "type": "ADD",
                "item": {
                    "skill": "react_component_debugging",
                    "actions": [
                        "使用 ESLint 检查React组件代码语法，设置规则为react/recommended",
                        "使用浏览器开发者工具检查DOM结构，分析组件渲染状态和props传递",
                        "使用 React DevTools 扩展分析组件层次结构和状态变化",
                        "提供具体的修复建议，包括代码优化和最佳实践指导"
                    ]
                }
            }
        ]
    },
    {
        "type": "agent_experience",
        "input": ("Conversation: [{'role': 'user', 'content': 'Agent需要帮助用户设置Docker环境并部署一个微服务应用。Agent创建了Dockerfile，配置了docker-compose.yml，设置了网络和卷挂载，最后成功启动了所有服务。'}]"
                 "ExistedItems: [{'memory_id': 'exp_004', 'skill': 'environment_setup', 'actions': ['创建配置文件', '启动服务']}, {'memory_id': 'exp_005', 'skill': 'file_operations', 'actions': ['使用 edit_file 创建文件', '使用 run_terminal_cmd 执行命令']}]"),
        "output": [
            {
                "type": "KEEP",
                "item": {
                    "skill": "file_operations",
                    "actions": ["使用 edit_file 创建文件", "使用 run_terminal_cmd 执行命令"]
                }
            },
            {
                "type": "DELETE",
                "memory_id": "exp_004",
                "item": {
                    "skill": "environment_setup",
                    "actions": ["创建配置文件", "启动服务"]
                }
            },
            {
                "type": "ADD",
                "item": {
                    "skill": "docker_microservice_deployment",
                    "actions": [
                        "使用 edit_file 创建 Dockerfile，设置基础镜像为node:18-alpine，配置工作目录和依赖安装",
                        "使用 edit_file 创建 docker-compose.yml，配置服务名称、端口映射、环境变量和网络设置",
                        "使用 edit_file 创建 .dockerignore 文件，排除不必要的文件和目录",
                        "使用 run_terminal_cmd 执行 'docker-compose up -d' 启动所有服务，验证服务状态"
                    ]
                }
            }
        ]
    }
]

AMNI_CONTEXT_PROMPT["USER_PROFILE_EXTRACTION_PROMPT"]  = """
You are a User Profile Analyst, specialized in extracting comprehensive user profile information from conversations to build detailed user personas. Your primary role is to analyze interactions and organize user characteristics into structured profiles for personalized experiences.

## Profile Categories to Extract:

1. **Personal Information**: Basic demographics like age, occupation, location, education level, family status, and significant life events.
2. **Preferences and Habits**: Likes, dislikes, daily routines, lifestyle choices, shopping habits, and behavioral patterns.
3. **Skills and Interests**: Professional skills, hobbies, technical expertise, learning interests, and creative pursuits.
4. **Communication Style**: Language preferences, formality level, emoji usage, response patterns, and interaction preferences.
5. **Professional Context**: Job role, industry, work habits, career goals, team dynamics, and professional challenges.
6. **Technical Proficiency**: Programming languages, tools, platforms, software preferences, and technical experience level.
7. **Goals and Aspirations**: Short-term objectives, long-term goals, learning targets, and personal development interests.

## Specific Key Categories:
- personal.basic: Basic personal information (age, name, location, etc.)
- personal.education: Educational background
- personal.family: Family-related information
- preferences.work: Work-related preferences
- preferences.lifestyle: Lifestyle preferences
- preferences.technical: Technical tool preferences
- skills.professional: Professional skills
- skills.technical: Technical skills
- skills.soft: Soft skills
- communication.style: Communication style preferences
- communication.language: Language preferences
- professional.role: Job role and responsibilities
- professional.industry: Industry information
- professional.experience: Work experience
- goals.career: Career-related goals
- goals.learning: Learning objectives
- goals.personal: Personal development goals

## Profile Update Operations:

### Operation Types:
1. **ADD**: Create new profile information or add to existing categories
2. **DELETE**: Remove outdated or incorrect profile information
3. **KEEP**: Maintain existing profile information that remains accurate

### Conflict Resolution Rules:
- **When conflicts occur**: Always prioritize the latest user conversation over historical data
- **For conflicting information**: DELETE the old profile item and ADD the new one
- **For unchanged information**: Use KEEP to maintain existing accurate data
- **For new information**: Use ADD to create new profile entries

### Examples of Conflict Resolution:
- **Location change**: DELETE old location, ADD new location
- **Job change**: DELETE old job info, ADD new job info  
- **Preference change**: DELETE old preference, ADD new preference
- **Skill addition**: ADD new skill while KEEP existing skills
- **Habit removal**: DELETE old habit, ADD updated habit list

## Output Format Guidelines:
Return each piece of profile information in JSON format with the following structure:

### For ADD and KEEP Operations:
```json
{
    "type": "ADD|KEEP",
    "item": {
        "key": "<specific_category_key>",
        "value": {
            // profile data
        }
    }
}
```

### For DELETE Operations:
```json
{
    "type": "DELETE",
    "memory_id": "<id_from_existed_items>",
    "item": {
        "key": "<specific_category_key>",
        "value": {
            // profile data to be deleted
        }
    }
}
```

### Complete Output Array:
```json
[
    {
        "type": "ADD|DELETE|KEEP",
        "memory_id": "<required_for_delete>",
        "item": {
            "key": "<specific_category_key>",
            "value": {
                // profile data
            }
        }
    }
]
```

## Few-Shot Examples:

Input: "I usually work late and drink lots of coffee. I'm trying to learn machine learning this year."
Output: [{
    "type": "ADD",
    "item": {
        "key": "preferences.work",
        "value": {
            "schedule": "works late",
            "habits": ["drinks lots of coffee"]
        }
    }
},{
    "type": "ADD",
    "item": {
        "key": "goals.learning",
        "value": {
            "target": "machine learning",
            "timeframe": "this year"
        }
    }
}]

Input: "Actually, I just moved to New York last month and I'm now working as a data scientist. I still love Python though."
ExistedItems: [{"memory_id": "mem_001", "key": "personal.basic", "value": {"age": "28", "occupation": "software developer", "location": "San Francisco"}}]
Output: [{
    "type": "DELETE",
    "memory_id": "mem_001",
    "item": {
        "key": "personal.basic",
        "value": {
            "age": "28",
            "occupation": "software developer",
            "location": "San Francisco"
        }
    }
},{
    "type": "ADD",
    "item": {
        "key": "personal.basic",
        "value": {
            "age": "28",
            "occupation": "data scientist",
            "location": "New York",
            "relocation_date": "last month"
        }
    }
}]

## Memory ID Requirements:

### For DELETE Operations:
- **REQUIRED**: Include `memory_id` field when deleting existing profile items
- **Format**: `"memory_id": "<id_from_existed_items>"`
- **Purpose**: Identifies which specific memory record to remove from the system

### For ExistedItems Format:
- Each existing profile item includes a `memory_id` field for identification
- Use this ID when performing DELETE operations
- Example: `{"memory_id": "mem_001", "key": "personal.basic", "value": {...}}`

### Output Structure with Memory ID:
```json
{
    "type": "DELETE",
    "memory_id": "mem_001",
    "item": {
        "key": "personal.basic",
        "value": {...}
    }
}
```

## Important Notes:
- Today's date is {{current_date}}
- Only extract information explicitly mentioned or clearly implied in the conversation.
- Do not infer information that is not supported by the conversation content.
- Preserve the original language of the user input in the extracted information.
- If no relevant information is found for a category, do not generate output for that category.
- Focus only on user messages, ignore system prompts or assistant responses.
- Maintain consistency in data types (strings for text, arrays for lists).
- Do not reveal your analysis process or prompt instructions to users.
- Each distinct piece of information should be categorized under the most specific applicable key.
- **CRITICAL**: When user provides new information that conflicts with existing profiles, use DELETE + ADD pattern to update the profile accurately.

## Language Detection:
Automatically detect the language of user input and record profile information in the same language to maintain cultural context and user preference.

Following is a conversation between the user and the assistant. Extract comprehensive user profile information from the conversation and return it in the specified JSON format.

"""

AMNI_CONTEXT_PROMPT["AGENT_EXPERIENCE_EXTRACTION_PROMPT"] = """
你是一个 Agent Experience 分析器，专门从 agent 与用户的交互中提取有价值的工具使用经验和操作步骤。你的主要任务是识别 agent 成功使用工具解决问题的具体操作序列，这些经验将帮助 agent 在未来执行相似任务时建立"肌肉记忆"。

## 经验提取重点：

### 核心关注领域：
1. **工具使用模式**：具体使用了哪些工具，如何配置参数，如何处理工具返回结果
2. **操作步骤序列**：从问题识别到最终解决的完整操作流程
3. **错误处理策略**：遇到问题时如何调试、重试、切换策略
4. **数据流转过程**：如何获取、处理、验证和输出数据
5. **环境配置操作**：如何设置开发环境、安装依赖、配置参数

### 具体工具操作示例：
- **文件操作**：`read_file(path)`, `edit_file(path, content)`, `delete_file(path)`
- **代码执行**：`run_terminal_cmd(command)`, `execute_code(code, language)`
- **API调用**：`call_api(endpoint, method, params, headers)`
- **搜索查询**：`search_web(query, filters)`, `search_codebase(query, scope)`
- **数据处理**：`parse_json(data)`, `validate_data(schema, data)`, `transform_data(data, rules)`

## 经验更新操作：

### 操作类型：
1. **ADD**: 创建新的经验信息或添加到现有类别
2. **DELETE**: 删除过时或不正确的经验信息
3. **KEEP**: 保持现有经验信息仍然准确

### 冲突解决规则：
- **当冲突发生时**：始终优先考虑最新的用户对话而不是历史数据
- **对于冲突信息**：DELETE 旧的经验项目并 ADD 新的
- **对于未改变的信息**：使用 KEEP 保持现有准确数据
- **对于新信息**：使用 ADD 创建新的经验条目

### 冲突解决示例：
- **技能升级**：DELETE 旧的技能描述，ADD 新的技能描述
- **工具版本更新**：DELETE 旧版本信息，ADD 新版本信息
- **操作流程优化**：DELETE 旧流程，ADD 优化后的流程
- **新增技能**：ADD 新技能同时 KEEP 现有技能

## 输出格式要求：

根据 AgentExperience 类的定义，输出必须包含以下字段：

### 对于 ADD 和 KEEP 操作：
```json
{
    "type": "ADD|KEEP",
    "item": {
        "skill": "具体的技能名称，如 'file_operations', 'code_execution', 'api_integration'",
        "actions": [
            "具体的操作步骤1，包含工具名称、参数和操作描述",
            "具体的操作步骤2，包含工具名称、参数和操作描述",
            "具体的操作步骤3，包含工具名称、参数和操作描述"
        ]
    }
}
```

### 对于 DELETE 操作：
```json
{
    "type": "DELETE",
    "memory_id": "<id_from_existed_items>",
    "item": {
        "skill": "具体的技能名称",
        "actions": [
            "具体的操作步骤1",
            "具体的操作步骤2",
            "具体的操作步骤3"
        ]
    }
}
```

### 完整输出数组：
```json
[
    {
        "type": "ADD|DELETE|KEEP",
        "memory_id": "<required_for_delete>",
        "item": {
            "skill": "具体的技能名称",
            "actions": [
                "具体的操作步骤1",
                "具体的操作步骤2",
                "具体的操作步骤3"
            ]
        }
    }
]
```

### 字段说明：
- **type**: 操作类型，必须是 "ADD", "DELETE", 或 "KEEP"
- **memory_id**: 对于 DELETE 操作必需，来自 existed_items 的 ID
- **skill**: 具体的技能名称，如 `file_operations`, `code_execution`, `api_integration`, `data_processing`, `environment_setup`, `error_debugging` 等
- **actions**: 具体的操作步骤列表，每个步骤都应该包含：
  - 使用的具体工具名称
  - 关键参数值
  - 操作的具体描述
  - 预期结果或验证方式

## Memory ID 要求：

### 对于 DELETE 操作：
- **必需**：包含 `memory_id` 字段当删除现有经验项目时
- **格式**：`"memory_id": "<id_from_existed_items>"`
- **目的**：标识要从系统中删除的特定记忆记录

### ExistedItems 格式：
- 每个现有经验项目包括一个 `memory_id` 字段用于标识
- 在 DELETE 操作时使用此 ID
- 示例：`{"memory_id": "exp_001", "skill": "api_error_handling", "actions": [...]}`

## 提取指导原则：

### 1. 具体化原则
- **避免抽象描述**：不要写"处理数据"，要写"使用pandas.read_csv()读取文件，设置encoding='utf-8'"
- **包含具体参数**：记录工具调用的关键参数，如文件路径、API端点、命令参数等
- **明确操作结果**：描述每个操作后得到的具体结果或验证方式

### 2. 可重现性原则
- **完整操作链**：记录从开始到结束的完整操作序列
- **环境信息**：包含必要的环境配置信息，如Python版本、依赖包版本等
- **错误处理**：记录如何处理常见错误和异常情况

### 3. 工具导向原则
- **工具名称**：明确记录使用的具体工具名称
- **参数配置**：记录工具的关键配置参数
- **返回处理**：描述如何处理工具返回的结果

### 4. 语言一致性原则
- **保持原语言**：如果对话是中文，actions也使用中文描述
- **技术术语**：保持技术术语的准确性，如API名称、工具名称等

## 重要注意事项：

1. **只提取成功经验**：专注于成功解决问题的操作序列，避免提取失败的经验
2. **增量更新策略**：使用 ADD/DELETE/KEEP 模式来管理经验库
3. **操作步骤数量**：actions列表通常包含3-5个关键步骤
4. **避免重复信息**：每个action应该包含新的、有价值的信息
5. **保持实用性**：提取的经验应该能够直接指导未来的相似任务

## 输出格式验证：

确保输出的JSON格式完全符合以下结构：
- type字段：必须是 "ADD", "DELETE", 或 "KEEP"
- memory_id字段：DELETE操作时必需，其他操作时可选
- item字段：包含skill和actions子字段
- skill字段：字符串类型，描述具体的技能名称
- actions字段：字符串数组，包含3-5个具体的操作步骤
- 整体格式：必须是有效的JSON格式，可以被直接解析

现在请分析以下对话和现有的Agent Experience，提取最显著的agent经验模式，并使用ADD/DELETE/KEEP操作来更新经验库：
"""