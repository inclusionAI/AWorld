desc = 'who can sort items in shopping site according to intent, can only be called on product list pages'


class ShoppingPriceSorter:
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
        sorter_response = ''
        if ('descending' in intent or 'most expensive' in intent) and '?q=' in url:
            sorted_url = url
            i_q = sorted_url.find('?q=')
            sorted_url = sorted_url[:i_q] + 'index/' + sorted_url[i_q:]
            sorted_url += '&product_list_order=price'
            sorted_url += '&product_list_dir=desc'
            sorter_response = (
                f'price sorter: In summary, the next action I will perform is ```goto [{sorted_url}]```'
            )
        elif ('ascending' in intent or 'least expensive' in intent) and '?q=' in url:
            sorted_url = url
            i_q = sorted_url.find('?q=')
            sorted_url = sorted_url[:i_q] + 'index/' + sorted_url[i_q:]
            sorted_url += '&product_list_order=price'
            sorted_url += '&product_list_dir=asc'
            sorter_response = (
                f'price sorter: In summary, the next action I will perform is ```goto [{sorted_url}]```'
            )
        return sorter_response