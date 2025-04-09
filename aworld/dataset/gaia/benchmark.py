import json
from pathlib import Path
from typing import Any, Dict, Tuple

from aworld.logs.util import logger


class GAIABenchmark:

    def __init__(
        self,
        data_dir: str,
    ):
        self.data_dir = Path(data_dir)
        self._data = {}

    def download(self):
        r"""Download the GAIA dataset."""
        from huggingface_hub import snapshot_download

        snapshot_download(
            repo_id="gaia-benchmark/GAIA",
            repo_type="dataset",
            local_dir=self.data_dir,
            local_dir_use_symlinks=True,
        )

    def load(self, force_download=False):
        r"""Load the GAIA dataset.

        Args:
            force_download (bool, optional): Whether to
                force download the data.
        """
        if force_download:
            logger.info("Force downloading data.")
            self.download()

        # Define validation and test directories
        valid_dir = self.data_dir / "2023/validation"
        test_dir = self.data_dir / "2023/test"

        # Load metadata for both validation and test datasets
        for path, label in zip([valid_dir, test_dir], ["valid", "test"]):
            self._data[label] = []
            with open(path / "metadata.jsonl", "r") as f:
                lines = f.readlines()
                for line in lines:
                    data = json.loads(line)
                    if data["task_id"] == "0-0-0-0-0":
                        continue
                    if data["file_name"]:
                        data["file_name"] = path / data["file_name"]
                    self._data[label].append(data)
        for key in self._data.keys():
            for task in self._data[key]:
                self._prepare_task(task)
        return self._data

    def _prepare_task(self, task: Dict[str, Any]) -> Tuple[bool, str]:
        r"""Prepare the task by validating and enriching its data."""
        if task["file_name"]:

            if isinstance(task["file_name"], Path):
                task["file_name"] = str(task["file_name"])

            file_path = Path(task["file_name"])
            if not file_path.exists():
                logger.info(f"Skipping task because file not found: {file_path}")
                return False, f"Skipping task because file not found: {file_path}"
            if file_path.suffix in [".pdf", ".docx", ".doc", ".txt"]:
                task[
                    "Question"
                ] += f" Here are the necessary document files: {file_path}"

            elif file_path.suffix in [".jpg", ".jpeg", ".png"]:
                task["Question"] += f" Here are the necessary image files: {file_path}"

            elif file_path.suffix in [".xlsx", "xls", ".csv"]:
                task[
                    "Question"
                ] += f" Here are the necessary table files: {file_path}, for processing excel file, you can write python code and leverage excel toolkit to process the file step-by-step and get the information."

            elif file_path.suffix in [".py"]:
                task["Question"] += f" Here are the necessary python files: {file_path}"

            else:
                task["Question"] += f" Here are the necessary files: {file_path}"

        return task
