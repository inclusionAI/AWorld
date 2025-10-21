desc = 'who can sort items in classifieds site according to intent, can only be called on classifieds site, should always be called after every action.'


class ClassifiedsPriceSorter:
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
        # Skip processing if the response contains code-type markers
        if '```type [' in aa_response:
            return ''

        sorter_response = ''

        # Case 1: Search results page with keyword-based search
        if '/index.php?page=search' in url and '&sPattern=' in url:
            sorted_url = url

            # Handle descending price sorting (e.g., "most expensive")
            if 'descending' in intent or 'most expensive' in intent:
                # Replace or add sorting parameter to price
                if '&sOrder=dt_pub_date' in sorted_url:
                    sorted_url = sorted_url.replace('&sOrder=dt_pub_date', '&sOrder=i_price')
                else:
                    sorted_url += '&sOrder=i_price'

                # Ensure descending order is applied
                if '&iOrderType=desc' not in sorted_url:
                    sorted_url += '&iOrderType=desc'

            # Handle ascending price sorting (e.g., "cheapest" or "least expensive")
            elif (
                'ascending' in intent
                or 'least expensive' in intent
                or 'cheapest' in intent
            ):
                # Replace or add sorting parameter to price
                if '&sOrder=dt_pub_date' in sorted_url:
                    sorted_url = sorted_url.replace('&sOrder=dt_pub_date', '&sOrder=i_price')
                else:
                    sorted_url += '&sOrder=i_price'

                # Ensure ascending order is applied
                if '&iOrderType=asc' not in sorted_url:
                    sorted_url += '&iOrderType=asc'

            # Make sure the view mode is set to "gallery"
            if '&sShowAs=gallery' not in sorted_url:
                sorted_url += '&sShowAs=gallery'

            sorter_response = (
                f'price sorter: In summary, the next action I will perform is '
                f'```goto [{sorted_url}]```'
            )

        # Case 2: Search results page with category-based search
        elif '/index.php?page=search' in url and '&sCategory=' in url:
            sorted_url = url

            # Ensure gallery view mode is enabled
            if '&sShowAs=gallery' not in sorted_url:
                sorted_url += '&sShowAs=gallery'
                sorter_response = (
                    f'price sorter: In summary, the next action I will perform is '
                    f'```goto [{sorted_url}]```'
                )

        return sorter_response