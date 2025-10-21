desc = 'who can return you the image description of the post image when you are at the post detailed page or comment page, can only be called on reddit site.'

import io
import base64
import requests
from io import BytesIO
from PIL import Image, JpegImagePlugin
from openai import OpenAI


class RedditImageDescriptor:
    def __init__(self, LLM_MODEL_NAME="", LLM_API_KEY="", LLM_BASE_URL="", temperature=0):
        self.LLM_MODEL_NAME = LLM_MODEL_NAME
        self.LLM_API_KEY = LLM_API_KEY
        self.LLM_BASE_URL = LLM_BASE_URL
        self.temperature = temperature

        self.client = OpenAI(
            api_key=self.LLM_API_KEY,
            base_url=self.LLM_BASE_URL,
            timeout=60000,
        )

    def image_to_base64(self, img):
        """Convert a PIL image to a base64-encoded string."""
        format = 'png'

        if isinstance(img, JpegImagePlugin.JpegImageFile):
            format = 'jpg'

        format = 'JPEG' if format == 'jpg' else 'PNG'

        buffered = io.BytesIO()
        img.save(buffered, format=format)
        img_byte = buffered.getvalue()
        img_base64 = base64.b64encode(img_byte).decode('utf-8')
        return img_base64

    def url_to_pil_image(self, url):
        """Download image from a given URL and return it as a PIL Image."""
        response = requests.get(url)
        response.raise_for_status()
        image_data = BytesIO(response.content)
        return Image.open(image_data)

    def run(self, intent, input_img, som_page_screenshot_img, ori_page_screenshot_img, page, vlm_request):
        """Main entry: extract and describe Reddit post image content using an LLM."""
        response = ''
        try:
            img_xpath = '//*[@id="main"]/article[1]/div/div[1]/div/a/img'
            img_locator = page.locator(f'xpath={img_xpath}')

            if img_locator.count() > 0:
                img_url = img_locator.get_attribute('src')
                img = self.url_to_pil_image(img_url)
                img_data = self.image_to_base64(img)

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
                                'text': (
                                    f"You will receive a task. To complete this task, please obtain "
                                    f"relevant information from the picture, such as the release time "
                                    f"of the movie, the quantity of certain items, etc. Control your "
                                    f"description to be as concise as possible (under 300 words). "
                                    f"The task you received is: {intent}. The picture is as follows:"
                                )
                            },
                            {
                                'type': 'image_url',
                                'image_url': {'url': f'data:image/png;base64,{img_data}'}
                            }
                        ]
                    }
                ]

                llm_response = self.client.chat.completions.create(
                    model=self.LLM_MODEL_NAME,
                    messages=messages,
                    temperature=self.temperature,
                    max_tokens=1024,
                )

                description = llm_response.choices[0].message.content
                slave_response = (
                    f"I saw the picture in this post, its description is: {description}. "
                    f"Use this information to take your action."
                )
                return slave_response

        except Exception as e:
            print(e)
            return ''

        return response
