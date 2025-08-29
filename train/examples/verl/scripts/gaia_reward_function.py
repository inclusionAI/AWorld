import re
from aworld.logs.util import logger


def gaia_reward_func(data_source, solution_str, ground_truth, extra_info=None):
  pattern = r'<answer>(.*?)</answer>'
  comp_match = re.search(pattern, solution_str, re.DOTALL | re.MULTILINE)
  logger.warn(f"GaiaAnswerMatch|content:{solution_str}, comp_match:{comp_match}, sol:{ground_truth}")
  if not comp_match:
      return 0.0
  return 1.0
