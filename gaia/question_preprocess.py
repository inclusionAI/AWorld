import os
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from datasets import load_dataset
from loguru import logger


def add_file_path(task: Dict[str, Any]):
    if task["file_name"]:
        # import pdb;pdb.set_trace()
        # if task["file_path"]:
        #     if isinstance(task["file_path"], Path):
        #         task["file_path"] = str(task["file_path"])

        # file_path = Path(task["file_name"])
        file_path = Path("/Users/yuchengyue/AWorld_mcp/gaia/gaia_dataset/2023/validation/" + task["file_name"])
        # if not file_path.exists():
        #     logger.info(f"Skipping task because file not found: {file_path}")
        #     return False, f"Skipping task because file not found: {file_path}"
        if file_path.suffix in [".pdf", ".docx", ".doc", ".txt"]:
            task["Question"] += f" Here are the necessary document files: {file_path}"

        elif file_path.suffix in [".jpg", ".jpeg", ".png"]:
            task["Question"] += f" Here are the necessary image files: {file_path}"

        elif file_path.suffix in [".xlsx", "xls", ".csv"]:
            task[
                "Question"
            ] += f" Here are the necessary table files: {file_path}, for processing excel file, you can use the excel tool or write python code to process the file step-by-step and get the information."

        elif file_path.suffix in [".py"]:
            task["Question"] += f" Here are the necessary python files: {file_path}"

        else:
            task["Question"] += f" Here are the necessary files: {file_path}"

    return task