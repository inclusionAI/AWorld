import asyncio

from examples.gaia.mcp_servers.mcp_browser import browser_use
from examples.gaia.mcp_servers.mcp_pdf import count_text_occurrences
from examples.gaia.mcp_servers.mcp_wayback import get_archived_page_content, list_available_versions


def test_wayback():
    asyncio.run(list_available_versions())
    asyncio.run(get_archived_page_content())


def test_browser():
    asyncio.run(browser_use(task="Open google.com and search baidu"))


def test_pdf():
    result = asyncio.run(
        count_text_occurrences(
            file_path="/Users/arac/Desktop/AWorld/gaia-benchmark/GAIA/2023/test/8f697523-6988-4c4f-8d72-760a45681f68.pdf",
            text_options=[r"[0-9]+", r"'", r'"', "!"],
        )
    )
    print(result)


if __name__ == "__main__":
    # test_wayback()
    # test_browser()
    test_pdf()
