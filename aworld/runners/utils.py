# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import List

from aworld.logs.util import logger


def endless_detect(loop_detect: List[str], endless_threshold: int, root_agent_name: str):
    if not loop_detect:
        return False

    threshold = endless_threshold
    last_agent_name = root_agent_name
    count = 1
    for i in range(len(loop_detect) - 2, -1, -1):
        if last_agent_name == loop_detect[i]:
            count += 1
        else:
            last_agent_name = loop_detect[i]
            count = 1

        if count >= threshold:
            logger.warning("detect loop, will exit the loop.")
            return True

    if len(loop_detect) > 6:
        last_agent_name = None
        # latest
        for j in range(1, 3):
            for i in range(len(loop_detect) - j, 0, -2):
                if last_agent_name and last_agent_name == (loop_detect[i], loop_detect[i - 1]):
                    count += 1
                elif last_agent_name is None:
                    last_agent_name = (loop_detect[i], loop_detect[i - 1])
                    count = 1
                else:
                    last_agent_name = None
                    break

                if count >= threshold:
                    logger.warning(f"detect loop: {last_agent_name}, will exit the loop.")
                    return True

    return False
