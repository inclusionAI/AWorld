import json
from collections import Counter
from pathlib import Path

from loguru import logger

if __name__ == "__main__":
    data_dir = Path("/Users/arac/Desktop/AWorld/gaia-benchmark/GAIA/2023/test")
    dataset = []
    with open(data_dir / "metadata.jsonl", "r", encoding="utf-8") as metaf:
        lines = metaf.readlines()
        for line in lines:
            data = json.loads(line)
            data["level"] = data["Level"] or ""
            if data["file_name"]:
                data["file_name"] = data_dir / data["file_name"]
            dataset.append(data)

    submission = [
        {
            "task_id": entry["task_id"],
            "model_answer": "",
            "reasoning_trace": "",
            "level": entry["Level"] or "",
        }
        for entry in dataset
    ]

    result_dir = Path("/Users/arac/Desktop/AWorld/gaia-benchmark/logs/test/all")
    with open(result_dir / "results_0520_extracted.json", "r", encoding="utf-8") as f:
        entries = json.loads(f.read())

    for entry in entries:
        for sub in submission:
            if entry["task_id"] == sub["task_id"]:
                # sub["model_answer"] = entry["response"]
                sub["model_answer"] = entry["answer"]
                break

    # Count records by level in dataset
    dataset_level_counts = Counter(item["level"] for item in dataset)
    logger.info("\nDataset records by level:")
    for level in sorted(dataset_level_counts.keys()):
        logger.info(f"  Level {level}: {dataset_level_counts[level]} records")
    logger.info("\n Total records:", sum(dataset_level_counts.values()))

    # Create a mapping of task_id to level from dataset
    task_id_to_level = {item["task_id"]: item["level"] for item in dataset}

    # Count records by level in submission
    submission_levels = [
        task_id_to_level.get(item["task_id"])
        for item in submission
        if item["task_id"] in task_id_to_level and item["model_answer"] != ""
    ]
    submission_level_counts = Counter(submission_levels)
    logger.info("\nSubmission records by level:")
    for level in sorted(submission_level_counts.keys()):
        logger.info(f"  Level {level}: {submission_level_counts[level]} records")
    logger.info("\n Total records:", sum(submission_level_counts.values()))

    # Finally, report the submission to a JSONL file
    json_path = result_dir / "submission_final.jsonl"
    with open(json_path, "w", encoding="utf-8") as f:
        for sub in submission:
            f.write(json.dumps(sub) + "\n")
    logger.success(f"Submission file generated successfully!\n>>> Path: {json_path}")
