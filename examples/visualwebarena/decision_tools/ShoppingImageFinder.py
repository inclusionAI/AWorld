desc = 'who can find the desired image and go to its detailed page.'


class ShoppingImageFinder:
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
        if 'stop' in aa_response and ('image url' in intent or 'image link' in intent):
            srcs = []
            try:
                root = page.locator(
                    'xpath=//*[@id="maincontent"]/div[3]/div/div[2]/div[2]/div[2]/div[2]/div[1]/div[3]'
                )
                imgs = root.locator('xpath=.//img').all()
                for img in imgs:
                    src = img.get_attribute('src')
                    srcs.append(src)
            except Exception as e:
                print(e)
            answer = ', '.join(srcs)
            response = f'```stop [{answer}]```'
        return response