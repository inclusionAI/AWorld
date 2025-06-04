import asyncio
import os

from examples.gaia.mcp_servers.mcp_audio import mcp_audio_metadata
from examples.gaia.mcp_servers.mcp_browser import browser_use
from examples.gaia.mcp_servers.mcp_csv import read_csv_file
from examples.gaia.mcp_servers.mcp_excel import read_excel_sheet
from examples.gaia.mcp_servers.mcp_image import mcp_image_recognition
from examples.gaia.mcp_servers.mcp_pdf import count_text_occurrences
from examples.gaia.mcp_servers.mcp_txt import find_text_in_which_line
from examples.gaia.mcp_servers.mcp_vector_store import index_text_file, search_text
from examples.gaia.mcp_servers.mcp_wayback import get_archived_page_content, list_available_versions
from examples.gaia.mcp_servers.mcp_yahoo_finance import get_historical_data


def test_yahoo():
    result = asyncio.run(
        get_historical_data(
            symbol="AUDUSD=X",
            start="2020-01-01",
            end="2020-01-02",
            interval="1d",
        )
    )
    print(result)


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


def test_excel():
    excel_file_path = os.path.expanduser(
        "~/Desktop/AWorld/gaia-benchmark/GAIA/2023/test/4033181f-1988-476b-bc33-6da0f96d7bd0.xlsx"
    )
    excel_file = asyncio.run(read_excel_sheet(excel_file_path, sheet_name="Sheet1"))
    print(excel_file)


def test_txt():
    txt_file_path = os.path.expanduser(
        "~/Desktop/AWorld/gaia-benchmark/GAIA/2023/test/f1ba834a-3bcb-4e55-836c-06cc1e2ccb9f.txt"
    )
    print(asyncio.run(find_text_in_which_line(txt_file_path, "culprit")))


def test_image():
    image_path = os.path.expanduser(
        "~/Desktop/AWorld/gaia-benchmark/GAIA/2023/test/e14448e9-5243-4b07-86e1-22e657f96bcf.jpg"
    )
    print(
        os.getenv("IMAGE_LLM_MODEL_NAME", "gpt-4o"),
    )
    question = "What animal is in this image?"
    result = mcp_image_recognition(question, image_path)
    print(result)


if __name__ == "__main__":
    # test_wayback()
    # test_browser()
    # test_pdf()
    # test_audio()
    # test_vector_store()
    # test_csv()
    # test_excel()
    # test_yahoo()
    # test_txt()
    test_image()
