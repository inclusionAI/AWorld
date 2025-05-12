from re import compile as re_compile
from re import search
from typing import Final, Iterable, TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from wsgiref.types import StartResponse, WSGIApplication, WSGIEnvironment

HTTP_REQUEST_METHOD: Final = "http.request.method"
HTTP_FLAVOR: Final = "http.flavor"
HTTP_HOST: Final = "http.host"
HTTP_SCHEME: Final = "http.scheme"
HTTP_USER_AGENT: Final = "http.user_agent"
HTTP_SERVER_NAME: Final = "http.server_name"
SERVER_ADDRESS: Final = "server.address"
SERVER_PORT: Final = "server.port"
URL_PATH: Final = "url.path"
URL_QUERY: Final = "url.query"
CLIENT_ADDRESS: Final = "client.address"
CLIENT_PORT: Final = "client.port"

HTTP_REQUEST_BODY_SIZE: Final = "http.request.body.size"
HTTP_REQUEST_HEADER: Final = "http.request.header"
HTTP_REQUEST_SIZE: Final = "http.request.size"
HTTP_RESPONSE_BODY_SIZE: Final = "http.response.body.size"
HTTP_RESPONSE_HEADER: Final = "http.response.header"
HTTP_RESPONSE_SIZE: Final = "http.response.size"
HTTP_RESPONSE_STATUS_CODE: Final = "http.response.status_code"


def collect_request_attributes(environ: WSGIEnvironment):

    attributes: dict[str] = {}

    request_method = environ.get("REQUEST_METHOD", "")
    request_method = request_method.upper()
    attributes[HTTP_REQUEST_METHOD] = request_method
    attributes[HTTP_FLAVOR] = environ.get("SERVER_PROTOCOL", "")
    attributes[HTTP_SCHEME] = environ.get("wsgi.url_scheme", "")
    attributes[HTTP_SERVER_NAME] = environ.get("SERVER_NAME", "")
    attributes[HTTP_HOST] = environ.get("HTTP_HOST", "")
    host_port = environ.get("SERVER_PORT")
    if host_port:
        attributes[SERVER_PORT] = host_port
    target = environ.get("RAW_URI")
    if target is None:
        target = environ.get("REQUEST_URI")
    if target:
        path, query = _parse_url_query(target)
        attributes[URL_PATH] = path
        attributes[URL_QUERY] = query
    remote_addr = environ.get("REMOTE_ADDR", "")
    attributes[CLIENT_ADDRESS] = remote_addr
    attributes[CLIENT_PORT] = environ.get("REMOTE_PORT", "")
    remote_host = environ.get("REMOTE_HOST")
    if remote_host and remote_host != remote_addr:
        attributes[CLIENT_ADDRESS] = remote_host
    attributes[HTTP_USER_AGENT] = environ.get("HTTP_USER_AGENT", "")
    return attributes


def url_disabled(url: str, excluded_urls: Iterable[str]) -> bool:
    """
    Check if the url is disabled.
    Args:
        url: The url to check.
        excluded_urls: The excluded urls.
    Returns:
        True if the url is disabled, False otherwise.
    """
    if excluded_urls is None:
        return False
    regex = re_compile("|".join(excluded_urls))
    return  search(regex, url)


def _parse_url_query(url: str):
    parsed_url = urlparse(url)
    path = parsed_url.path
    query_params = parsed_url.query
    return path, query_params
