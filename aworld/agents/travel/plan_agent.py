# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import re
import time
import traceback
from typing import Dict, Any, Optional, List, Union, Tuple

from langchain_core.messages import HumanMessage, BaseMessage, AIMessage, ToolMessage, SystemMessage
from pydantic import ValidationError

from aworld.agents.travel.prompts import PROMPT_TEMPLATE
from aworld.config.common import Tools
from aworld.core.agent.base import AgentFactory, Agent, AgentResult
from aworld.agents.browser.utils import convert_input_messages, extract_json_from_model_output, estimate_messages_tokens
from aworld.agents.browser.common import AgentState, AgentStepInfo, AgentHistory, PolicyMetadata, AgentBrain
from aworld.config.conf import AgentConfig
from aworld.core.common import Observation, ActionModel, ActionResult
from aworld.logs.util import logger
from aworld.agents.browser.prompts import AgentMessagePrompt
from aworld.agents.browser.agent import Trajectory


@AgentFactory.register(name="travel_plan_agent", desc="travel plan agent")
class TravelPlanAgent(Agent):
    def __init__(self, conf: AgentConfig, **kwargs):
        super(TravelPlanAgent, self).__init__(conf, **kwargs)
        self.state = AgentState()
        self.settings = conf.model_dump()
        if conf.llm_provider == 'openai':
            conf.llm_provider = 'chatopenai'

        # Note: Removed _message_manager initialization as it's no longer used
        # Initialize trajectory
        self.trajectory = Trajectory()
        self._init = False

    def reset(self, options: Dict[str, Any]):
        super(TravelPlanAgent, self).reset(options)

        # Reset trajectory
        self.trajectory = Trajectory()

        # Note: Removed _message_manager initialization as it's no longer used
        # _estimate_tokens_for_messages method now directly uses functions from utils.py
        self._init = True

    def _log_message_sequence(self, input_messages: List[BaseMessage]) -> None:
        """Log the sequence of messages for debugging purposes"""
        logger.info(f"[agent] 🔍 Invoking LLM with {len(input_messages)} messages")
        logger.info("[agent] 📝 Messages sequence:")
        for i, msg in enumerate(input_messages):
            prefix = msg.type
            logger.info(f"[agent] Message {i + 1}: {prefix} ===================================")
            if isinstance(msg.content, list):
                for item in msg.content:
                    if item.get('type') == 'text':
                        logger.info(f"[agent] Text content: {item.get('text')}")
                    elif item.get('type') == 'image_url':
                        # Only print the first 30 characters of image URL to avoid printing entire base64
                        image_url = item.get('image_url', {}).get('url', '')
                        if image_url.startswith('data:image'):
                            logger.info(f"[agent] Image: [Base64 image data]")
                        else:
                            logger.info(f"[agent] Image URL: {image_url[:30]}...")
            else:
                content = str(msg.content)
                chunk_size = 500
                for j in range(0, len(content), chunk_size):
                    chunk = content[j:j + chunk_size]
                    if j == 0:
                        logger.info(f"[agent] Content: {chunk}")
                    else:
                        logger.info(f"[agent] Content (continued): {chunk}")

            if isinstance(msg, AIMessage) and hasattr(msg, 'tool_calls') and msg.tool_calls:
                for tool_call in msg.tool_calls:
                    logger.info(f"[agent] Tool call: {tool_call.get('name')} - ID: {tool_call.get('id')}")
                    args = str(tool_call.get('args', {}))[:1000]
                    logger.info(f"[agent] Tool args: {args}...")

    def policy(self,
               observation: Observation,
               info: Dict[str, Any] = None, **kwargs) -> Union[List[ActionModel], None]:
        start_time = time.time()

        if self._init is False:
            self.reset({"task": observation.content})

        # Save current observation to state for message construction
        self.state.last_result = observation.action_result

        if self.conf.max_steps <= self.state.n_steps:
            logger.info('Last step finishing up')

        logger.info(f'[agent] step {self.state.n_steps}')

        # Use the new method to build messages, passing the current observation
        input_messages = self.build_messages_from_trajectory_and_observation(observation=observation)

        # Note: Special message addition has been moved to build_messages_from_trajectory_and_observation

        # Estimate token count
        tokens = self._estimate_tokens_for_messages(input_messages)

        llm_result = None
        output_message = None
        try:
            # Log the message sequence
            self._log_message_sequence(input_messages)

            output_message, llm_result = self._do_policy(input_messages)

            if not llm_result:
                logger.error("[agent] ❌ Failed to parse LLM response")
                return [ActionModel(agent_name=self.name())]  ## tmp stop

            self.state.n_steps += 1

            # No longer need to remove the last state message
            # self._message_manager._remove_last_state_message()

            if self.state.stopped or self.state.paused:
                logger.info('Browser gent paused after getting state')
                return [ActionModel(agent_name=self.name())]  ## tmp stop

            tool_action = llm_result.actions

            # Add the current step to the trajectory
            self.trajectory.add_step(input_messages, observation, info, output_message, llm_result)

        except Exception as e:
            logger.warning(traceback.format_exc())
            # No longer need to remove the last state message
            # self._message_manager._remove_last_state_message()
            logger.error(f"[agent] ❌ Error parsing LLM response: {str(e)}")

            # Create an AgentResult object with an empty actions list
            error_result = AgentResult(
                current_state=AgentBrain(
                    evaluation_previous_goal="Failed due to error",
                    memory=f"Error occurred: {str(e)}",
                    next_goal="Recover from error"
                ),
                actions=[]  # Empty actions list
            )

            # Add the error state to the trajectory
            self.trajectory.add_step(input_messages, observation, info, output_message, error_result)

            return [ActionModel(tool_name=Tools.BROWSER.value, action_name="stop")]
        finally:
            if llm_result:
                # Remove duplicate trajectory addition, as it's already been added in the try block
                # self.trajectory.add_step(observation, info, llm_result) - Already executed in try block

                # Only keep the history_item creation part
                metadata = PolicyMetadata(
                    number=self.state.n_steps,
                    start_time=start_time,
                    end_time=time.time(),
                    input_tokens=tokens,
                )
                self._make_history_item(llm_result, observation, observation.action_result, metadata)
            else:
                logger.warning("no result to record!")

        return tool_action

    def _do_policy(self, input_messages: list[BaseMessage]) -> Tuple[AIMessage, AgentResult]:
        THINK_TAGS = re.compile(r'<think>.*?</think>', re.DOTALL)

        def _remove_think_tags(text: str) -> str:
            """Remove think tags from text"""
            return re.sub(THINK_TAGS, '', text)

        input_messages = self._convert_input_messages(input_messages)
        output_message = None
        try:
            # print(input_messages)
            output_message = self.llm.invoke(input_messages)
            # print(output_message)
            if not output_message or not output_message.content:
                logger.warning("[agent] LLM returned empty response")
                # return AgentResult(current_state=AgentBrain(evaluation_previous_goal="", memory="", next_goal=""),
                #                    actions=[ActionModel(tool_name=Tools.BROWSER.value, action_name="stop")])
                return output_message, AgentResult(
                    current_state=AgentBrain(evaluation_previous_goal="", memory="", next_goal=""),
                    actions=[ActionModel(agent_name=self.name())])
        except:
            logger.error(f"[agent] Response content: {output_message}")

        if self.model_name == 'deepseek-reasoner':
            output_message.content = _remove_think_tags(output_message.content)
        try:
            parsed_json = extract_json_from_model_output(output_message.content)
            logger.info((f"llm response: {parsed_json}"))
            agent_brain = AgentBrain(**parsed_json['current_state'])
            actions = parsed_json.get('action')
            result = []
            if not actions:
                actions = parsed_json.get("actions")
            if not actions:
                logger.warning("agent not policy an action.")
                return output_message, AgentResult(current_state=agent_brain,
                                                   actions=[ActionModel(agent_name=self.name(),
                                                                        action_name="done")])

            for action in actions:
                if "action_name" in action:
                    action_name = action['action_name']
                    # if not browser_action:
                    if action_name not in ("", "", "", ""):
                        logger.warning(f"Unsupported action: {action_name}")
                    action_model = ActionModel(
                        agent_name=action_name,
                        params=action.get('params', {}))
                    result.append(action_model)
                else:
                    for k, v in action.items():
                        action_model = ActionModel(agent_name=k, params=v)
                        # action_model = ActionModel(tool_name=Tools.BROWSER.value, action_name=k, params=v)
                        result.append(action_model)
            return output_message, AgentResult(current_state=agent_brain, actions=result)
        except (ValueError, ValidationError) as e:
            logger.warning(f'Failed to parse model output: {output_message} {str(e)}')
            raise ValueError('Could not parse response.')

    def _convert_input_messages(self, input_messages: list[BaseMessage]) -> list[BaseMessage]:
        """Convert input messages to the correct format"""
        if self.model_name == 'deepseek-reasoner' or self.model_name.startswith('deepseek-r1'):
            return convert_input_messages(input_messages, self.model_name)
        else:
            return input_messages

    def _make_history_item(self,
                           model_output: AgentResult | None,
                           state: Observation,
                           result: list[ActionResult],
                           metadata: Optional[PolicyMetadata] = None) -> None:
        content = ""
        if hasattr(state, 'dom_tree') and state.dom_tree is not None:
            if hasattr(state.dom_tree, 'element_tree'):
                content = state.dom_tree.element_tree.__repr__()
            else:
                content = str(state.dom_tree)

        history_item = AgentHistory(model_output=model_output,
                                    result=state.action_result,
                                    metadata=metadata,
                                    content=content,
                                    base64_img=state.image if hasattr(state, 'image') else None)

        self.state.history.history.append(history_item)

    def build_messages_from_trajectory_and_observation(self, observation: Optional[Observation] = None) -> List[
        BaseMessage]:
        """
        Build complete message history from trajectory and current observation

        Args:
            observation: Current observation object, if None current observation won't be added
        """
        messages = []

        system_prompt = PROMPT_TEMPLATE
        # Add system message
        system_message = SystemMessage(content=system_prompt)
        if isinstance(system_message, tuple):
            system_message = system_message[0]
        messages.append(system_message)

        # Add task context (if any)
        if self.settings.get('message_context'):
            context_message = HumanMessage(content='Context for the task' + self.settings.message_context)
            messages.append(context_message)

        # Add task message
        task_message = HumanMessage(
            content=f'Your ultimate task is: """{self.task}""". If you achieved your ultimate task, stop everything and use the done action in the next step to complete the task. If not, continue as usual.'
        )
        messages.append(task_message)

        # Add example output
        placeholder_message = HumanMessage(content='Example output:')
        messages.append(placeholder_message)

        # Add example tool call
        tool_calls = [
            {
                'name': 'AgentOutput',
                'args': {
                    'current_state': {
                        'evaluation_previous_goal': 'Success - I completed search and gets the url',
                        'memory': 'search compeleted and gets url',
                        'next_goal': 'extract information from the related url',
                    },
                    'action': [{'browser_agent': {"task": "goto xxx(url) to extract content about xxx."}}],
                },
                'id': '1',
                'type': 'tool_call',
            }
        ]
        example_tool_call = AIMessage(
            content='',
            tool_calls=tool_calls,
        )
        messages.append(example_tool_call)

        # Add first tool message with "Plan agent started" content
        messages.append(ToolMessage(content='Plan agent started', tool_call_id='1'))

        # Add task history marker
        messages.append(HumanMessage(content='[Your task history memory starts here]'))

        # Add available file paths (if any)
        if self.settings.get('available_file_paths'):
            filepaths_msg = HumanMessage(
                content=f'Here are file paths you can use: {self.settings.get("available_file_paths")}')
            messages.append(filepaths_msg)

        # Add messages from the history trajectory
        for obs, info, result in self.trajectory.get_history():
            # Add observation message
            step_info = AgentStepInfo(number=len(messages) - 7, max_steps=self.conf.max_steps)

            # Build observation message
            if hasattr(obs, 'dom_tree') and obs.dom_tree:
                state_message = AgentMessagePrompt(
                    obs,
                    obs.action_result,
                    include_attributes=self.settings.get('include_attributes'),
                    step_info=step_info,
                ).get_user_message(True)
                messages.append(state_message)
            elif obs.content:
                messages.append(HumanMessage(content=str(obs.content)))

            # Add agent response
            if result:
                # Create AI message
                output_data = result.model_dump(mode='json', exclude_unset=True)
                output_data["action"] = [{action.action_name: action.params} for action in result.actions]
                if "actions" in output_data:
                    del output_data["actions"]

                # Calculate tool_id based on trajectory history. If no actions yet, start with ID 1
                tool_id = 1 if len(self.trajectory.get_history()) == 0 else len(self.trajectory.get_history()) + 1
                tool_calls = [
                    {
                        'name': 'AgentOutput',
                        'args': output_data,
                        'id': str(tool_id),
                        'type': 'tool_call',
                    }
                ]

                ai_message = AIMessage(
                    content='',
                    tool_calls=tool_calls,
                )
                messages.append(ai_message)

                # Add empty tool message after each AIMessage
                messages.append(ToolMessage(content='', tool_call_id=str(tool_id)))

        # Add current observation - using the passed observation parameter instead of self.state.current_observation
        if observation:
            step_info = AgentStepInfo(number=self.state.n_steps, max_steps=self.conf.max_steps)
            if hasattr(observation, 'dom_tree') and observation.dom_tree:
                state_message = AgentMessagePrompt(
                    observation,
                    self.state.last_result,
                    include_attributes=self.settings.get('include_attributes'),
                    step_info=step_info,
                ).get_user_message(self.settings.get('use_vision'))
                messages.append(state_message)
            if observation.content:
                messages.append(HumanMessage(content=observation.content))

        # Add special message for the last step
        # Note: Moved here from policy method to centralize all message building logic
        if self.conf.max_steps <= self.state.n_steps:
            last_step_message = f"""
                   Now comes your last step. Use only the "done" action now. No other actions - so here your action sequence must have length 1.
                   \nIf the task is not yet fully finished as requested by the user, set success in "done" to false! E.g. if not all steps are fully completed.
                   \nIf the task is fully finished, set success in "done" to true.
                   \nInclude everything you found out for the ultimate task in the done text.
               """
            messages.append(HumanMessage(content=[{'type': 'text', 'text': last_step_message}]))

        return messages

    def _estimate_tokens_for_messages(self, messages: List[BaseMessage]) -> int:
        """Roughly estimate token count for message list"""
        # Note: Using estimate_messages_tokens function from utils.py instead of calling _message_manager
        # This decouples the dependency on MessageManager
        return estimate_messages_tokens(
            messages,
            image_tokens=self.settings.get('image_tokens', 800),
            estimated_characters_per_token=self.settings.get('estimated_characters_per_token', 3)
        )
