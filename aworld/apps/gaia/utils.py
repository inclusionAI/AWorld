import re
import string
from typing import Any, Dict, List, Literal, Optional, Tuple, Union

from aworld.logs.util import logger


def normalize_str(input_str, remove_punct=True) -> str:
    no_spaces = re.sub(r"\s", "", input_str)
    if remove_punct:
        translator = str.maketrans("", "", string.punctuation)
        return no_spaces.lower().translate(translator)
    else:
        return no_spaces.lower()


def split_string(s: str, char_list: Optional[List[str]] = None) -> list[str]:
    if char_list is None:
        char_list = [",", ";"]
    pattern = f"[{''.join(char_list)}]"
    return re.split(pattern, s)


def normalize_number_str(number_str: str) -> float:
    for char in ["$", "%", ","]:
        number_str = number_str.replace(char, "")
    try:
        return float(number_str)
    except ValueError:
        logger.error(f"String {number_str} cannot be normalized to number str.")
        return float("inf")


def question_scorer(model_answer: str, ground_truth: str) -> bool:
    def is_float(element: Any) -> bool:
        try:
            float(element)
            return True
        except ValueError:
            return False

    try:
        if is_float(ground_truth):
            logger.info(f"Evaluating {model_answer} as a number.")
            normalized_answer = normalize_number_str(model_answer)
            return normalized_answer == float(ground_truth)

        elif any(char in ground_truth for char in [",", ";"]):
            logger.info(f"Evaluating {model_answer} as a comma separated list.")
            gt_elems = split_string(ground_truth)
            ma_elems = split_string(model_answer)

            if len(gt_elems) != len(ma_elems):
                logger.warning(
                    "Answer lists have different lengths, returning False.",
                    UserWarning,
                )
                return False

            comparisons = []
            for ma_elem, gt_elem in zip(ma_elems, gt_elems):
                if is_float(gt_elem):
                    normalized_ma_elem = normalize_number_str(ma_elem)
                    comparisons.append(normalized_ma_elem == float(gt_elem))
                else:
                    ma_elem = normalize_str(ma_elem, remove_punct=False)
                    gt_elem = normalize_str(gt_elem, remove_punct=False)
                    comparisons.append(ma_elem == gt_elem)
            return all(comparisons)
        else:
            logger.info(f"Evaluating {model_answer} as a string.")
            ma_elem = normalize_str(model_answer)
            gt_elem = normalize_str(ground_truth)
            return ma_elem == gt_elem
    except Exception as e:
        logger.error(f"Error during evaluation: {e}")
        return False


def _check_task_completed(task_id, _results) -> bool:
    for data in _results:
        if data["task_id"] == task_id:
            return True
    return False


def _generate_summary(_results) -> Dict[str, Any]:
    r"""Generate and return a summary of the benchmark results."""
    correct = sum(result["score"] for result in _results)
    return {
        "total": len(_results),
        "correct": correct,
        "accuracy": correct / len(_results) if len(_results) > 0 else 0,
    }
