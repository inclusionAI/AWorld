from typing import List

from ... import ApplicationContext
from . import Neuron

TODO_PROMPT = """
<todo_guide>
🎯 **TODO Management Best Practices Guide**

## 📋 Task Initialization Phase
- **Multi-step Task Detection**: For complex tasks, first use the `get_todo` tool to check if an existing todo exists
- **Task Planning**: If todo is empty or doesn't exist, use the `add_todo` tool to create a detailed step-by-step execution plan
- **Task Breakdown**: Break down complex tasks into specific, executable subtasks, ensuring each step has clear objectives and deliverables

## 🔄 TODO Management During Execution
- **Progress Tracking**: Regularly use the `get_todo` tool to read current task status and confirm execution progress
- **Real-time Updates**: Immediately update todo using appropriate tools after completing each subtask, marking `[ ]` as `[x]`
- **Result Recording**: Add brief descriptions of key results or outputs after marking completed tasks
- **Dynamic Adjustment**: Promptly adjust or add new subtasks based on new situations encountered during execution

## ✅ Task Completion Criteria
- **Completeness Check**: Only consider the entire task complete when all items in todo are marked as finished
- **Result Verification**: Ensure each completed subtask has corresponding outputs or results
- **Quality Confirmation**: Verify that final results meet the original requirements

## 📝 TODO Format Specifications
- **Incomplete Tasks**: Use `[ ] task description` format
- **Completed Tasks**: Use `[x] task description` format
- **Task Descriptions**: Maintain rigor and precision, avoid ambiguous expressions
  - ✅ Correct: `[ ] Fetch user profile configuration API endpoint`
  - ❌ Incorrect: `[ ] Get user information` (too vague)
  - ✅ Correct: `[x] Parse JSON response data`
  - ❌ Incorrect: `[x] Process data` (not specific enough)
- **Result Recording**: Add brief result descriptions after completed tasks
  - Format: `[x] task description - Result: specific output or key information, Knowledge ID: kb_identifier`
- **Task Grouping**: Related tasks can be grouped using indentation
- **Line Break Rules**: Use two line breaks between each task item

## 🛠️ Tool Usage Guidelines
- **Read Tasks**: Use `get_todo` tool to get current task list
- **Add Tasks**: Use `add_todo` tool to add new subtasks
- **Update Status**: Use appropriate tools to update task completion status
- **Adjust Plan**: Dynamically modify task plan based on actual circumstances

## 🎨 Task Description Best Practices
- **Specificity**: Descriptions should be specific, avoid vague terms
- **Verifiability**: Each task should have clear completion criteria
- **Independence**: Each subtask should be relatively independent for easy progress tracking
- **Priority**: Complex tasks can include priority or dependency annotations

## 📚 Practical Example

**Scenario**: Setting up a data processing pipeline

**Initial TODO Creation**:
```
- [ ] Connect to source database and extract user data
- [ ] Transform data format and validate records
- [ ] Load processed data into target analytics warehouse
```

**During Execution - Progress Updates**:
```
- [x] Connect to source database and extract user data - Result: Successfully extracted 10,000 user records, Knowledge ID: kb_20241201_user_extract_001

- [ ] Transform data format and validate records
- [ ] Load processed data into target analytics warehouse
```

**Final Completion State**:
```
- [x] Connect to source database and extract user data - Result: Successfully extracted 10,000 user records, Knowledge ID: kb_20241201_user_extract_001

- [x] Transform data format and validate records - Result: Validated and transformed 9,850 clean records, Knowledge ID: kb_20241201_transform_002

- [x] Load processed data into target analytics warehouse - Result: All data loaded into analytics tables successfully, Knowledge ID: kb_20241201_load_003
```

**Key Takeaways from Example**:
- ✅ Clear, specific task descriptions
- ✅ Real-time progress updates with results
- ✅ Consistent formatting throughout execution
- ✅ Complete task tracking until all items marked as done
</todo_guide>
"""

class TodoNeuron(Neuron):
    """处理计划相关属性的Neuron"""

    async def desc(self, context: ApplicationContext, namespace: str = None, **kwargs):
        return TODO_PROMPT

    async def format_items(self, context: ApplicationContext, namespace: str = None, **kwargs) -> List[str]:
        todo_info = await context.get_todo_info()
        return [todo_info if todo_info else ""]

    async def format(self, context: ApplicationContext, items: List[str] = None, namespace: str = None, **kwargs) -> str:
        """组合计划信息"""
        if not items:
            items = await self.format_items(context, namespace, **kwargs)

        return "\n\n<todo_info>\n" + "\n".join(items) + "\n</todo_info>\n"
