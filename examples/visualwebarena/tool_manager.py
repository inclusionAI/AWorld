import os
import sys
parent_dir = os.path.dirname(os.path.abspath(__file__))
up_dir = parent_dir
for i in range(3):
    sys.path.append(up_dir)
    up_dir = os.path.dirname(up_dir)
from kutils import DEBUG, INFO, WARN, ERROR
import utils as u
import importlib
from llms.ais_requestor import get_lm_requestor
from get_messages import get_default_vlm_messages

class ToolManager:
    def __init__(self, model):
        self.model = model
        self.kq = get_lm_requestor(self.model)
        self.hint_path = f'{parent_dir}/hint_tools/'
        self.hint_tools = self.__read_tools(self.hint_path)
        self.decision_path = f'{parent_dir}/decision_tools/'
        self.decision_tools = self.__read_tools(self.decision_path)
        self.all_tools = {}
        self.all_tools.update(self.hint_tools)
        self.all_tools.update(self.decision_tools)

    def __request(self, prompt, input_img, som_page_screenshot_img):
        messages = get_default_vlm_messages(prompt, input_img, som_page_screenshot_img)
        while 1:
            try:
                response = self.kq.infer_messages(messages)
                break
            except Exception as e:
                ERROR(f'{self.model} {e}')
                response = 'stop []'
            u.wait(1)
        
        return response

    def get_all_tools(self) -> dict:
        return self.all_tools

    def get_hint_tools(self) -> dict:
        return self.hint_tools

    def get_decision_tools(self) -> dict:
        return self.decision_tools

    def __read_tools(self, folder_path) -> dict:
        result = {}
        for filename in os.listdir(folder_path):
            if filename.endswith(".py"):
                filepath = os.path.join(folder_path, filename)
                with open(filepath, 'r', encoding='utf-8') as f:
                    for line in f:
                        if "desc =" in line:
                            desc_text = line.strip()
                            desc_value = desc_text.split("desc =", 1)[1].strip()
                            desc_value = desc_value.replace('\'', '')
                            result[filename.replace('.py', '')] = desc_value
                            break
        return result

    def call_hint(self, name, intent, input_img, 
                   som_page_screenshot_img, ori_page_screenshot_img, page):
        path = self.hint_path.rstrip('/')
        module_path = path.split('/')[-2] + '.' + path.split('/')[-1]
        if module_path.endswith('.'): module_path = module_path[:-1]
        module_path = f"{module_path}.{name}"
        module = importlib.import_module(module_path)
        cls = getattr(module, name)
        obj = cls()
        response = obj.run(intent, input_img, som_page_screenshot_img, ori_page_screenshot_img, page, self.__request)
        return response

    def call_decision(self, name, intent, aa_response, url, input_img, 
                     som_page_screenshot_img, ori_page_screenshot_img, page):
        path = self.decision_path.rstrip('/')
        module_path = path.split('/')[-2] + '.' + path.split('/')[-1]
        if module_path.endswith('.'): module_path = module_path[:-1]
        module_path = f"{module_path}.{name}"
        module = importlib.import_module(module_path)
        cls = getattr(module, name)
        obj = cls()
        response = obj.run(intent, aa_response, url, input_img, som_page_screenshot_img, ori_page_screenshot_img, page, self.__request)
        return response
