desc = 'who can find the most similar post to the input image on reddit and go to its detailed page and commemt page, should only be called on reddit site.'

import os
import io
import json
import base64
from io import BytesIO

import requests
import imagehash
from openai import OpenAI
from PIL import Image, JpegImagePlugin

parent_dir = os.path.dirname(os.path.abspath(__file__))


class ImageSearcher:
    def __init__(
        self,
        LLM_MODEL_NAME="",
        LLM_API_KEY="",
        LLM_BASE_URL="",
        temperature=0.0,
    ):
        self.LLM_MODEL_NAME = LLM_MODEL_NAME
        self.LLM_API_KEY = LLM_API_KEY
        self.LLM_BASE_URL = LLM_BASE_URL
        self.temperature = temperature
        self.client = OpenAI(
            api_key = self.LLM_API_KEY,
            base_url = self.LLM_BASE_URL,
            timeout = 60000,
        )

    def get_ahash(self, img):
        return imagehash.average_hash(img)

    def get_phash(self, img):
        return imagehash.phash(img)

    def ahash_similarity(self, img1, img2):
        h1 = imagehash.average_hash(img1)
        h2 = imagehash.average_hash(img2)
        return 1 - (h1 - h2) / 64.0

    def llm_similarity(self, img1, img2):
        image_data1 = self.image_to_base64(img1)
        image_data2 = self.image_to_base64(img2)
        messages = [
            {
                'role': 'system',
                'content': 'You are a helpful assistant.'
            },
            {
                'role': 'user',
                'content': [
                    {
                        'type': 'text',
                        'text': "给出这两张图片的相似度，直接输出0~1.0中的值"
                    },
                    {
                        'type': 'image_url',
                        'image_url': {
                            'url': f'data:image/png;base64,{image_data1}'
                        }
                    },
                    {
                        'type': 'image_url',
                        'image_url': {
                            'url': f'data:image/png;base64,{image_data2}'
                        }
                    }
                ]
            }
        ]
        resp = ""
        try:
            response = self.client.chat.completions.create(
                model=self.LLM_MODEL_NAME,
                messages=messages,
                temperature=self.temperature,
                max_tokens=1024,
            )
            resp = response.choices[0].message.content
            resp = float(resp)
        except Exception as e:
            print(e)
        return resp

    def image_to_base64(self, img):
        format = 'png'
        if isinstance(img, JpegImagePlugin.JpegImageFile):
            format = 'jpg'
        if format == 'jpg':
            format = 'JPEG'
        elif format == 'png':
            format = 'PNG'
        buffered = io.BytesIO()
        img.save(buffered, format=format)
        img_byte = buffered.getvalue()
        img_base64 = base64.b64encode(img_byte).decode('utf-8')
        return img_base64

    def url_to_pil_image(self, url):
        response = requests.get(url)
        response.raise_for_status()
        image_data = BytesIO(response.content)
        image = Image.open(image_data)
        return image

    def run(
        self,
        intent,
        aa_response,
        url,
        input_img,
        som_page_screenshot_img,
        ori_page_screenshot_img,
        page,
        vlm_request
    ):
        response = ''
        try:
            img_input_ahash = self.get_ahash(input_img)

            with open(parent_dir + "/all_reddit_items_with_img.json", "r") as f:
                all_item = json.load(f)

            max_sim = 0
            max_item = {}
            for item in all_item:
                img_ahash = imagehash.hex_to_hash(item["img_ahash"])
                a_sim = 1 - (img_input_ahash - img_ahash) / 64.0
                if a_sim > max_sim:
                    max_sim = a_sim
                    max_item = item

            if max_sim >= 0.9:
                comment_url = max_item.get("comment_url")
                slave_response = f'In summary, the next action I will perform is ```goto [{comment_url}]```'
                return slave_response
        except Exception as e:
            print(e)
            return ''
        return response