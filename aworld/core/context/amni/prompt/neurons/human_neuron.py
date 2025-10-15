from typing import List

from ... import ApplicationContext
from . import Neuron
from .neuron_factory import neuron_factory

HUMAN_TOOL_PROMPT = """
<human>
**human Tool**
1. **Critical Limitation**: The human tool should only be invoked when encountering specific authorization barriers that prevent automated tools from continuing execution. It is prohibited to use the human tool for general information gathering, analysis, or reporting tasks.

2. **Prohibited Scenarios**: Never use the human tool for:
   - Information gathering and analysis tasks
   - General web browsing and content retrieval
   - Data processing and reporting
   - General decision making or task completion
   - Seeking routine operation approval
   - Never ask users to confirm execution plans, todo list, step-by-step procedures, or task breakdowns. The agent should execute plans autonomously without seeking user approval for routine operations.

3. **Tool Input Prefix Rules**: When using the human tool, add the following prefixes before tool input:
   - If the human tool requires user approval/confirmation, add prefix `1|` to the human tool input prefix
   - If the human tool requires user text input, add prefix `2|` to the human tool input prefix
   - If the human tool requires user file upload, add prefix `3|` to the human tool input prefix

4. **Specific Examples** (only for authorization barriers):
   - Encountering login page: `1|Current operation requires user login, please perform the relevant login operation on the page, then continue execution`
   - Encountering captcha input page: `1|Current operation requires user to input captcha, please input the captcha on the page, then continue execution`
   - Encountering password input page: `1|Current operation requires user to input password, please perform password input operation on the page, then continue execution`
   - Encountering payment page: `1|Current operation requires user payment, please perform the relevant payment operation on the page, then continue execution`
   - Requiring sudo privileges: `1|Current operation requires administrator privileges, please confirm whether to authorize execution of this command`
   - For text input: `2|Please provide your body type and height information, for example: body type (underweight/standard/overweight), height (cm)`
   - For file upload: `3|Please upload your ID card photo`
   - For image upload: `3|Please upload an image`

5. **Tool Types That Can Trigger Human Tool**: The human tool should only be triggered when the following tool types encounter specific authorization barriers:
   - Browser operation tools - only for login/authentication/payment pages
   - Terminal tools - only for sudo/administrator privilege requests
   - File system tools - only for protected directory access
</human>
"""


@neuron_factory.register(name="human", desc="Human tool neuron", prio=5)
class HumanNeuron(Neuron):
    """Neuron for handling human tool related properties"""

    async def desc(self, context: ApplicationContext, namespace: str = None, **kwargs) -> str:
        return HUMAN_TOOL_PROMPT

    async def format_items(self, context: ApplicationContext, namespace: str = None, **kwargs) -> List[str]:
        return []

    async def format(self, context: ApplicationContext, items: List[str] = None, namespace: str = None, **kwargs) -> str:
        """Combine human tool information"""
        return ""
