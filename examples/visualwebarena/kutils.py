# coding: utf-8
# Copyright (c) 2024 Antfin, Inc. All rights reserved.
import logging
logging.getLogger("PIL.PngImagePlugin").setLevel(logging.CRITICAL + 1)
logging.getLogger("PIL.Image").setLevel(logging.CRITICAL + 1)
logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
logging.getLogger("TiffImagePlugin").setLevel(logging.CRITICAL + 1)
logging.getLogger('PIL.TiffImagePlugin').setLevel(logging.CRITICAL + 1)
logging.getLogger("remote_connection").setLevel(logging.CRITICAL + 1)
logging.getLogger("api").setLevel(logging.CRITICAL + 1)
logging.getLogger("http").setLevel(logging.CRITICAL + 1)
logging.getLogger('oss2').setLevel(logging.CRITICAL + 1)
logging.getLogger('matplotlib').setLevel(logging.CRITICAL + 1)
logging.getLogger('_api').setLevel(logging.CRITICAL + 1)
logging.getLogger("transformers.datasets").setLevel(logging.CRITICAL + 1)
logging.getLogger("pyhocon.config_parser").setLevel(logging.CRITICAL + 1)
logging.getLogger("httpcore").setLevel(logging.CRITICAL + 1)
logging.getLogger("openai").setLevel(logging.CRITICAL + 1)
logging.getLogger("pytorch.config").setLevel(logging.CRITICAL + 1)
logging.getLogger("playwright._config").setLevel(logging.CRITICAL + 1)
logging.getLogger("httpx").setLevel(logging.CRITICAL + 1)
logging.getLogger('asyncio').setLevel(logging.CRITICAL + 1)
import warnings
warnings.filterwarnings("ignore", message="Option log_view_host has been replaced by logview_host and might be removed in a future release.")
warnings.filterwarnings("ignore", category=UserWarning, message="pkg_resources is deprecated as an API.*")
import sys
def get_os():
    if sys.platform.startswith('linux'):
        return "linux"
    elif sys.platform.startswith('win'):
        return "win"
    elif sys.platform.startswith('darwin'):
        return 'mac'
    return None

application = None
# application = 'arec'
logger = None
if get_os() == 'mac' or application != 'arec':
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.basicConfig(level=logging.DEBUG, format='%(threadName)s,%(filename)s,[%(funcName)s],%(lineno)d:%(message)s',)
    logger = logging
else:
    from arec_py_infra.log import log_util
    logger = log_util.get_logger('default')

DEBUG = logger.debug
INFO = logger.info
WARN = logger.warning
ERROR = logger.error
