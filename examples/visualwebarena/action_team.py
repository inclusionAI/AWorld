import os
import sys
parent_dir = os.path.dirname(os.path.abspath(__file__))
up_dir = parent_dir
for i in range(3):
    sys.path.append(up_dir)
    up_dir = os.path.dirname(up_dir)
from kutils import DEBUG, INFO, WARN, ERROR
import utils as u
import json
import bu_mas_prompts as mp
from agent_config import get_agent_config
from get_messages import get_default_vlm_messages
from aw_agent import AWAgent
from tool_manager import ToolManager

class ActionTeam():
    def __init__(self, model):
        self.model = model
        self.called_decision_tools = []

    def get_execution_agent_messages(
            self, 
            system_prompt, 
            task, 
            url, 
            obs,
            tabs,
            action_history,
            action_hint,
            input_img, 
            som_page_screenshot_img,
            ori_page_screenshot_img = None,
            last_som_img = None,
            last_ori_img = None,
        ):
        messages = [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": system_prompt,
                    }
                ]
            },
            {
                "role": "user",
                "content": [
                    {
                        'type': 'text', 
                        'text': f'Task: {task}', 
                    },
                    {
                        'type': 'text', 
                        'text': f'Current web page\'s URL: {url}',
                    
                    },
                    {
                        "type": "text",
                        "text": f'Observations: {obs}',
                    },
                    {
                        "type": "text",
                        "text": f'Open tabs: {tabs}',
                    },
                    {
                        "type": "text",
                        "text": f'Action history: {action_history}',
                    },
                    {
                        "type": "text",
                        "text": f'Action hint by human adviser: {action_hint}',
                    },
                ]
            }
        ]

        if input_img:
            input_img_msg = \
            [
                {
                    "type": "text",
                    "text": "User input image:"
                },
                {
                    "type": "image_url",
                    "image_url": {
                        'url': 'data:image/png;base64,' 
                    }
                }
            ]
            input_image_base64 = u.pil_image_to_base64(input_img)
            input_img_msg[-1]['image_url']['url'] += input_image_base64
            messages[1]['content'] += input_img_msg

        if last_som_img:
            last_som_img_msg = \
            [
                {
                    "type": "text",
                    "text": "Last page screenshot with interactable bounding boxes:"
                },
                {
                    "type": "image_url",
                    "image_url": {
                        'url': 'data:image/png;base64,' 
                    }
                }
            ]
            last_som_image_base64 = u.pil_image_to_base64(last_som_img)
            last_som_img_msg[-1]['image_url']['url'] += last_som_image_base64
            messages[1]['content'] += last_som_img_msg
        
        if ori_page_screenshot_img:
            ori_page_img_msg = \
            [
                {
                    "type": "text",
                    "text": "Current page screenshot:"
                },
                {
                    "type": "image_url",
                    "image_url": {
                        'url': 'data:image/png;base64,' 
                    }
                }
            ]
            ori_image_base64 = u.pil_image_to_base64(ori_page_screenshot_img)
            ori_page_img_msg[-1]['image_url']['url'] += ori_image_base64
            messages[1]['content'] += ori_page_img_msg

        page_img_msg = \
        [
            {
                "type": "text",
                "text": "Current page screenshot with interactable bounding boxes:"
            },
            {
                "type": "image_url",
                "image_url": {
                    'url': 'data:image/png;base64,' 
                }
            }
        ]
        ss_image_base64 = u.pil_image_to_base64(som_page_screenshot_img)
        page_img_msg[-1]['image_url']['url'] += ss_image_base64
        messages[1]['content'] += page_img_msg 
        return messages

    def next_action(self, output_response, intent, action_history, site_name, url, obs, tabs,
                    input_img, som_page_screenshot_img, ori_page_screenshot_img, page):
        tm = ToolManager(self.model)
        pr_master = mp.master.format(
            MEMBERS = json.dumps(tm.get_all_tools()), TASK = intent, 
            ACTION_HISTORY = action_history, SITE_NAME = site_name,
        )
        master_agent = AWAgent(
            name="Master Agent", 
            conf=get_agent_config(), 
            system_prompt=pr_master
        )
        ma_messages = get_default_vlm_messages(pr_master, input_img, som_page_screenshot_img)
        try:
            master_response = master_agent.pure_invoke_model(ma_messages)
        except Exception as e:
            ERROR(f'master agent error: {e}')
            master_response = 'none'
        if output_response: INFO(f'master: {master_response}')

        hint_response = ''
        for hint_tool in tm.get_hint_tools().keys():
            if hint_tool in master_response:
                hint_response += tm.call_hint(hint_tool, intent, input_img, som_page_screenshot_img, ori_page_screenshot_img, page)
                if not hint_response: ERROR(f'calling {hint_tool} failed')
                else: hint_response += '/n'
        if output_response: INFO(f'hint: {hint_response}')

        pr_aa = mp.execution_agent
        execution_agent = AWAgent(name="Execution Agent", conf=get_agent_config(), system_prompt=pr_aa)
        ea_messages = self.get_execution_agent_messages(
            pr_aa, intent, url, obs, tabs, action_history, hint_response, 
            input_img, som_page_screenshot_img, ori_page_screenshot_img)
        try:
            execution_response = execution_agent.pure_invoke_model(ea_messages)
        except Exception as e:
            ERROR(f'execution agent error: {e}')
            execution_response = 'In summary, the next action I will perform is ```wait```'
        if output_response: INFO(f'execution agent: {execution_response}')

        decision_response = ''
        for decision in tm.get_decision_tools().keys():
            if decision in master_response and decision not in self.called_decision_tools:
                decision_response += tm.call_decision(decision, intent, execution_response, url, input_img, som_page_screenshot_img, ori_page_screenshot_img, page)
                if not decision_response: ERROR(f'calling {decision} failed')
                else: 
                    decision_response += '/n'
                    self.called_decision_tools.append(decision)
        if output_response: INFO(f'decision: {decision_response}')

        if decision_response: response = decision_response
        else: response = execution_response

        action_info = {
            'pred_action_history': '',
            'pred_action_description': '',
            'pred_action': '',
            'pred_action_type': '',
            'pred_bbox': '',
            'pred_type_value': '',
            'pred_click_point': '',
            'parse_error_msg': '',
            'content_to_memo': '',
        }
        action_info['pred_action_description'] += f'\naction_team: {response}'
        return action_info, response