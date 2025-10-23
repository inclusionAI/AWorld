desc = 'who would upvote the current post when you are at the post detailed page or comment page.'


class UpVoter:
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
            url = page.url
            upvote_btn_xpath = 'xpath=//*[@id="main"]/article[1]/div/div[2]/form/button[1]'
            retract_title_xpath = 'xpath=//*[@id="main"]/article[1]/div/div[2]/form/span[1]'

            if page.locator(upvote_btn_xpath).count() > 0:
                if "Retract" in page.locator(retract_title_xpath).get_attribute('title'):
                    return ''

                btn = page.locator(upvote_btn_xpath)
                btn.scroll_into_view_if_needed()
                btn.wait_for(state="visible")
                btn.click(force=True)
                page.wait_for_load_state()

                page.goto(url)
                page.wait_for_load_state()

                btn = page.locator(upvote_btn_xpath)
                btn.scroll_into_view_if_needed()
                btn.wait_for(state="visible")
                btn.click(force=True)
                page.wait_for_load_state()

                slave_response = (
                    'I have upvoted this post. In summary, the next action I will perform is '
                    '```stop [I have upvoted this post]```'
                )
                return slave_response
        except Exception as e:
            print(e)
            return ''
        return response


