import asyncio
import os

from examples.gaia.mcp_servers.mcp_audio import mcp_audio_metadata
from examples.gaia.mcp_servers.mcp_browser import browser_use
from examples.gaia.mcp_servers.mcp_csv import read_csv_file
from examples.gaia.mcp_servers.mcp_pdf import count_text_occurrences
from examples.gaia.mcp_servers.mcp_vector_store import index_text_file, search_text
from examples.gaia.mcp_servers.mcp_wayback import get_archived_page_content, list_available_versions


def test_wayback():
    asyncio.run(list_available_versions())
    asyncio.run(get_archived_page_content())


def test_browser():
    asyncio.run(browser_use(task="Open google.com and search baidu"))


def test_pdf():
    result = asyncio.run(
        count_text_occurrences(
            file_path=(
                os.path.expanduser(
                    "~/Desktop/AWorld/gaia-benchmark/GAIA/2023/test/8f697523-6988-4c4f-8d72-760a45681f68.pdf"
                )
            ),
            text_options=[r"[0-9]+", r"'", r'"', "!"],
        )
    )
    print(result)


def test_audio():
    result = asyncio.run(
        mcp_audio_metadata(
            os.path.expanduser(
                "~/Desktop/AWorld/gaia-benchmark/GAIA/2023/test/5bbf523f-b902-4d7d-8e8d-212d00018733.mp3"
            )
        )
    )
    print(result)


def test_vector_store():
    chunks = asyncio.run(
        index_text_file(
            file_path=(
                os.path.expanduser(
                    "~/Desktop/AWorld/gaia-benchmark/GAIA/2023/test/8f697523-6988-4c4f-8d72-760a45681f68.pdf"
                )
            )
        )
    )
    print(f"The number of {chunks=}")
    results = asyncio.run(search_text(query="Philosophical Discourse of Modernity"))
    print(f"{results=}")


def test_csv():
    csv_file_path = os.path.expanduser("~/Desktop/AWorld/gaia-benchmark/fs-remake/banklist.csv")
    csv_file = read_csv_file(csv_file_path)
    print(csv_file)


if __name__ == "__main__":
    # test_wayback()
    # test_browser()
    # test_pdf()
    # test_audio()
    # test_vector_store()
    test_csv()
