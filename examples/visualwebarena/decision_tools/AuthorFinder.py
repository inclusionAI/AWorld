desc = 'who can find the author\'s all post when you are at the post detailed page or commemt page, and go to the author\'s page.'

class AuthorFinder:
    def run(self, intent, aa_response, url, input_img, som_page_screenshot_img, ori_page_screenshot_img, page, vlm_request):
        """Find the author's profile URL from the current page and return the next action command."""
        response = ''
        try:
            author_url = None
            author_xpath = '//*[@id="main"]/article[1]/div/div[1]/header/p/span/a[1]'
            author_element = page.locator(f'xpath={author_xpath}')

            if author_element.is_visible():
                href = author_element.get_attribute('href')
                author_url = f"http://localhost:9999{href}"
                slave_response = f"In summary, the next action I will perform is ```goto [{author_url}]```"
                return slave_response

        except Exception as e:
            print(e)
            return ''

        return response
