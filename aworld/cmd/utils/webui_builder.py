import os
import subprocess
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def build_webui(force_rebuild: bool = False) -> str:
    webui_path = Path(__file__).parent.parent / "web" / "webui"
    static_path = webui_path / "dist"

    if (not static_path.exists()) or force_rebuild:
        logger.warning(f"Build WebUI at {webui_path}")

        p = subprocess.Popen(
            ["sh", "-c", "npm install && npm run build"],
            cwd=webui_path,
        )
        p.wait()
        if p.returncode != 0:
            raise Exception(f"Failed to build WebUI, error code: {p.returncode}")
        else:
            logger.info("WebUI build successfully")

    return static_path
