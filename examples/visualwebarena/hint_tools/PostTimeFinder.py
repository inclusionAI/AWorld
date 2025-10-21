desc = 'who can find the post time when you are at the post detailed page or comment page.'

class PostTimeFinder:
    def run(self, intent, input_img, som_page_screenshot_img, ori_page_screenshot_img, page, vlm_request):
        """Extracts the post time from the page if available."""
        response = ''
        try:
            post_time = None
            time_xpath = '//*[@id="main"]/article[1]/div/div[1]/header/p/span/time'
            time_element = page.locator(f'xpath={time_xpath}')

            if time_element.is_visible():
                post_time = time_element.get_attribute('datetime')
                slave_response = f"The post time is {post_time}. The next action is ```stop [post time]```"
                return slave_response

        except Exception as e:
            print(e)
            return ''

        return response
