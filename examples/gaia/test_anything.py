import asyncio

from examples.gaia.mcp_servers.mcp_browser import browser_use
from examples.gaia.mcp_servers.mcp_wayback import get_archived_page_content, list_available_versions


def test_wayback():
    asyncio.run(list_available_versions())
    asyncio.run(get_archived_page_content())


def test_browser():
    asyncio.run(browser_use(task="Open google.com and search baidu"))


if __name__ == "__main__":
    test_wayback()
    test_browser()
