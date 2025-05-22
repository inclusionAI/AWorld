import argparse
import os

from dotenv import load_dotenv

from aworld.config.conf import AgentConfig
from examples.gaia.agent import GaiaAgent
from examples.gaia.prompt import system_prompt
from examples.gaia.runner import GaiaRunner, RunnerArguments

if __name__ == "__main__":
    load_dotenv()

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--split",
        type=str,
        default="validation",
        choices=["validation", "test"],
        help="Split of the dataset, e.g., validation, test",
    )
    parser.add_argument(
        "--skip",
        action="store_true",
        help="Skip the question if it has been processed before.",
    )
    parser.add_argument(
        "--level",
        type=lambda value: [int(digit) for digit in value.split(",")],
        nargs="?",
        help="Level of the question, e.g., 1, 2, 3",
    )
    parser.add_argument(
        "--q",
        type=str,
        nargs="?",
        help=("Question Index, e.g., 0-0-0-0-0. Highest priority: override other arguments if provided."),
    )
    parser.add_argument(
        "--slice",
        type=str,
        nargs="?",
        help="A continuous range of question indices, e.g., 0:300",
    )
    parser.add_argument(
        "--blacklist_file_path",
        type=str,
        nargs="?",
        help="Blacklist file path, e.g., blacklist.txt",
    )
    parser.add_argument(
        "--submit",
        action="store_true",
        help="Dump the submission result",
    )
    args = parser.parse_args()

    dataset_path = os.getenv("GAIA_DATASET_PATH")
    log_path = os.getenv("LOG_FILE_PATH")

    runner = GaiaRunner(
        agent=GaiaAgent(
            output_folder_path=log_path,
            name="gaia_agent",
            system_prompt=system_prompt,
            config=AgentConfig(
                llm_provider="openai",
                llm_model_name=os.getenv("LLM_MODEL_NAME", "gpt-4o"),
                llm_api_key=os.getenv("LLM_API_KEY", "your_openai_api_key"),
                llm_base_url=os.getenv("LLM_BASE_URL", "your_openai_base_url"),
            ),
            mcp_servers=[
                "e2b-server",
                # "filesystem",
                "terminal-controller",
                "excel",
                "calculator",
                # "playwright",
                "audio_server",
                "image_server",
                "video_server",
                "search_server",
                "download_server",
                "document_server",
                # "pdf_server",
                "pdf-reader-mcp",
                "browser_server",
                "youtube_server",
                "reasoning_server",
                "wikipedia_server",
            ],
        ),
        runner_args=RunnerArguments(
            split=args.split,
            level=args.level,
            q=args.q,
            slice=args.slice,
            blacklist_file_path=args.blacklist_file_path,
            skip=args.skip,
            is_submit=args.submit,
        ),
        dataset_folder_path=dataset_path,
        output_folder_path=log_path,
    )

    runner.submit()
