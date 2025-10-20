import os
import sys
parent_dir = os.path.dirname(os.path.abspath(__file__))
up_dir = parent_dir
for i in range(3):
    sys.path.append(up_dir)
    up_dir = os.path.dirname(up_dir)
from kutils import DEBUG, INFO, WARN, ERROR
import utils as u

def get_default_vlm_messages(prompt, input_img, som_page_screenshot_img):
    messages = []
    if input_img:
        messages = [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": "You are a helpful assistant." 
                    }
                ]
            },
            {
                "role": "user",
                "content": [
                    {
                        'type': 'text', 
                        'text': prompt, 
                    }, 
                    {
                        "type": "text",
                        "text": "IMAGES: (1) user input image"
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            'url': 'data:image/png;base64,' 
                        }
                    },
                    {
                        "type": "text",
                        "text": "(2) current page screenshot with interactable bounding boxes"
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            'url': 'data:image/png;base64,' 
                        }
                    }
                ]
            }
        ]
        input_image_base64 = u.pil_image_to_base64(input_img)
        messages[1]['content'][2]['image_url']['url'] += input_image_base64
        ss_image_base64 = u.pil_image_to_base64(som_page_screenshot_img)
        messages[1]['content'][4]['image_url']['url'] += ss_image_base64
    else:
        messages = [
            {
                "role": "system",
                "content": [
                    {
                        "type": "text",
                        "text": "You are a helpful assistant." 
                    }
                ]
            },
            {
                "role": "user",
                "content": [
                    {
                        'type': 'text', 
                        'text': prompt, 
                    }, 
                    {
                        "type": "text",
                        "text": "IMAGES: (1) current page screenshot"
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            'url': 'data:image/png;base64,' 
                        }
                    }
                ]
            }
        ]
        ss_image_base64 = u.pil_image_to_base64(som_page_screenshot_img)
        messages[1]['content'][2]['image_url']['url'] += ss_image_base64

    return messages
