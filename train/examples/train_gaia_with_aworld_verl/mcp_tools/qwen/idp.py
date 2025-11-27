import logging
import os
import json

from aworld.logs.util import logger

# Try to import IDP dependencies, fallback to mock implementation if not available
try:
    from alibabacloud_docmind_api20220711.client import Client as docmind_api20220711Client
    from alibabacloud_tea_openapi import models as open_api_models
    from alibabacloud_docmind_api20220711 import models as docmind_api20220711_models
    from alibabacloud_tea_util.client import Client as UtilClient
    from alibabacloud_tea_util import models as util_models
    from alibabacloud_credentials.client import Client as CredClient
    IDP_AVAILABLE = True
    logger.info("IDP_AVAILABLE=True")
except ImportError:
    IDP_AVAILABLE = False
    logger.info("Warning: IDP dependencies not available. IDP functionality will be disabled.")

class IDP():
    def __init__(self):
        if not IDP_AVAILABLE:
            logger.info("IDP not available - dependencies missing")
            self.client = None
            return
        config = open_api_models.Config(
            access_key_id=os.environ['ALIBABA_CLOUD_ACCESS_KEY_ID'],
            access_key_secret=os.environ['ALIBABA_CLOUD_ACCESS_KEY_SECRET']
        )
        config.endpoint = f'docmind-api.cn-hangzhou.aliyuncs.com'
        self.client = docmind_api20220711Client(config)

    def file_submit_with_url(self, file_url):
        if not IDP_AVAILABLE or not self.client:
            logger.info('IDP not available - skipping URL submission')
            return None

        logger.info('parsing with document url ', file_url)
        file_name = os.path.basename(file_url)
        request = docmind_api20220711_models.SubmitDocParserJobAdvanceRequest(
            file_url=file_url,
            file_name=file_name,
            reveal_markdown=True,
        )
        runtime = util_models.RuntimeOptions()
        result_dict = None
        try:
            response = self.client.submit_doc_parser_job_advance(request,runtime)
            result_dict = response.body.data.id
        except Exception as error:
            UtilClient.assert_as_string(error.message)

        return result_dict


    def file_submit_with_path(self, file_path):
        if not IDP_AVAILABLE or not self.client:
            logger.info('IDP not available - skipping path submission')
            return None

        logger.info(f'parsing with document local path {file_path}')
        file_name = os.path.basename(file_path)
        request = docmind_api20220711_models.SubmitDocParserJobAdvanceRequest(
            file_url_object=open(file_path, "rb"),
            file_name=file_name,
        )
        runtime = util_models.RuntimeOptions()
        result_dict = None
        try:
            response = self.client.submit_doc_parser_job_advance(request, runtime)
            result_dict = response.body.data.id
        except Exception as error:
            logger.info(error)
            UtilClient.assert_as_string(error.message)

        return result_dict

    def file_parser_query(self,fid):
        if not IDP_AVAILABLE or not self.client:
            logger.info('IDP not available - skipping query')
            return None, 'unavailable'

        request = docmind_api20220711_models.QueryDocParserStatusRequest(
            id=fid
        )
        try:
            response = self.client.query_doc_parser_status(request)
            NumberOfSuccessfulParsing = response.body.data
        except Exception as e:
            logger.info(e)
            return None
        status_parse = response.body.data.status
        NumberOfSuccessfulParsing = NumberOfSuccessfulParsing.__dict__
        responses = dict()
        for i in range(0, NumberOfSuccessfulParsing["number_of_successful_parsing"], 3000):
            request = docmind_api20220711_models.GetDocParserResultRequest(
                id=fid,
                layout_step_size=3000,
                layout_num=i
            )
            try:
                response = self.client.get_doc_parser_result(request)
                result = response.body.data
                if not responses:
                    responses = result
                else:
                    responses['layouts'].extend(result['layouts'])
            except Exception as error:
                return None,status_parse
        return responses,status_parse
