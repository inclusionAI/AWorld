desc = 'who can guide you to the specific category page when you are at the shopping site, can only be called on shopping site.'

import os
import re
import json
from typing import Union


def extract_text(text: str, from_text: str = None, to_text: str = None) -> list[str]:
    """Extract substring(s) between two markers."""
    if from_text is None and to_text is None:
        return [text]

    if to_text is None:
        return [text[text.find(from_text):]]

    if from_text is None:
        return [text[:text.find(to_text)]]

    pattern = re.escape(from_text) + r'(.*?)' + re.escape(to_text)
    matches = re.findall(pattern, text, re.DOTALL)
    return [m.strip() for m in matches] if matches else []


def read_json(json_file: str, encoding: str = 'utf-8-sig') -> Union[dict, list]:
    """Read a .json or .jsonl file into Python object."""
    if json_file.endswith('.json'):
        with open(json_file, 'r', encoding=encoding) as f:
            return json.load(f)

    if json_file.endswith('.jsonl'):
        with open(json_file, 'r', encoding=encoding) as f:
            return [json.loads(line) for line in f]

    return {}


CATEGORY_GUIDE_PROMPT = """You are a browser-use agent and will be provided with a task (maybe attached with an image) and the available categories. Your job is to output the category name according to the task, and lead the user to the specific interested category, along with your action reason and description.

To be successful, follow these rules:
1. You should only output category that is in the available categories.
2. You should only output one category at a time.

Your task:
{TASK}

Available categories:
{CATEGORIES}

Output using following format:
{{
    "reason": "",
    "description": "",
    "category": ""
}}
"""


class CategoryGuide:
    """Class to interpret model output and guide category-based navigation."""

    def __parse_shopping_guide_response(self, response: str):
        """Extract reason, description, and category from model output."""
        error_msg = ""
        try:
            response_json = extract_text(response, '```json', '```')[0]
        except Exception as e:
            response_json = response
            error_msg += f"{response}, error extracting json: {e}\n"

        def safe_extract(src, start, end, key):
            try:
                return extract_text(src, start, end)[0]
            except Exception as e:
                nonlocal error_msg
                error_msg += f"{src}, error extracting {key}: {e}\n"
                return ""

        reason = safe_extract(response_json, '"reason": "', '",', 'reason')
        description = safe_extract(response_json, '"description": "', '",', 'description')
        category = safe_extract(response_json, '"category": "', '"\n}', 'category')

        if error_msg:
            print(error_msg)
        return reason, description, category

    def run(self, intent, aa_response, url, input_img, som_page_screenshot_img,
            ori_page_screenshot_img, page, vlm_request):
        """Main execution entry for guiding the category navigation."""
        if not url.startswith('http://localhost:7770/'):
            return ""

        try:
            if 'category' in intent and 'Search for' not in intent and 'search results' not in intent:
                cate_file = os.path.join(os.path.dirname(__file__), 'shopping_categories.json')
                raw_categories = read_json(cate_file)

                categories = {c['class_name']: c['class_url'] for c in raw_categories}

                prompt = CATEGORY_GUIDE_PROMPT.format(
                    TASK=intent,
                    CATEGORIES=json.dumps(list(categories.keys()))
                )

                model_response = vlm_request(prompt, input_img, ori_page_screenshot_img)
                reason, desc, category_name = self.__parse_shopping_guide_response(model_response)

                category_url = categories.get(category_name, "")
                if not category_url.endswith('?'):
                    category_url += '?'

                if any(k in intent for k in ['least expensive', 'cheapest', 'ascending']):
                    category_url += 'product_list_order=price&product_list_dir=asc'
                elif any(k in intent for k in ['most expensive', 'more than']):
                    category_url += 'product_list_order=price&product_list_dir=desc'

                return f"In summary, the next action I will perform is ```goto [{category_url}]```"

        except Exception as e:
            print(f"[CategoryGuide Error] {e}")

        return ""


