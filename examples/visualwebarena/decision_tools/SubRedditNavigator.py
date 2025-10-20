desc = 'who can navigate to the subreddit page when you are at the post detailed page or comment page.'


class SubRedditNavigator():
    def run(
        self,
        intent,
        aa_response,
        url,
        input_img,
        som_page_screenshot_img,
        ori_page_screenshot_img,
        page,
        vlm_request,
    ):
        response = ''
        try:
            subreddit_url = None
            locator_xpath = 'xpath=//*[@id="main"]/article[1]/div/div[1]/header/p/span/a[2]'
            locator = page.locator(locator_xpath)
            if locator.is_visible():
                subreddit_url = "http://localhost:9999" + locator.get_attribute('href')
                slave_response = (
                    f'The subreddit page is {subreddit_url}. In summary, the next action I will perform is '
                    f'go to the subreddit page, which is ```goto [{subreddit_url}]```'
                )
                return slave_response
        except Exception as e:
            print(e)
            return ''
        return response
