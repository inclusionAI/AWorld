import json
from pathlib import Path

if __name__ == "__main__":
    data_dir = Path("/Users/arac/Desktop/AWorld/gaia-benchmark/GAIA/2023/test")
    dataset = []
    with open(data_dir / "metadata.jsonl", "r", encoding="utf-8") as metaf:
        lines = metaf.readlines()
        for line in lines:
            entry = json.loads(line)
            if entry["file_name"]:
                entry["file_name"] = data_dir / entry["file_name"]
            dataset.append(
                {
                    "task_id": entry["task_id"],
                    "model_answer": "",
                    "reasoning_trace": "",
                    "level": entry["Level"],
                }
            )

    # 6af95c8f-8cbf-4c12-b02c-f9a23cc1ecb9 not found in split validation.
    # Are you sure you submitted the correct file?
    result_dir = Path("/Users/arac/Desktop/AWorld/gaia-benchmark/logs/test/sample_60")
    submission = []
    with open(result_dir / "submission.jsonl", "r", encoding="utf-8") as f:
        for sub in f.readlines():
            submission.append(json.loads(sub))

    # Compare dataset and submission by task_id
    dataset_task_ids = set(item["task_id"] for item in dataset)
    submission_task_ids = set(item["task_id"] for item in submission)

    # Find missing records in submission
    missing_task_ids = dataset_task_ids - submission_task_ids

    # Print results
    print(f"Total dataset records: {len(dataset_task_ids)}")
    print(f"Total submission records: {len(submission_task_ids)}")

    if missing_task_ids:
        print(f"\nMissing records in submission: {len(missing_task_ids)}")
        print("Missing task_ids:")
        for task_id in sorted(missing_task_ids):
            print(f"  - {task_id}")
    else:
        print("\nAll dataset records are present in the submission.")

    # Check for extra records in submission
    extra_task_ids = submission_task_ids - dataset_task_ids
    if extra_task_ids:
        print(f"\nExtra records in submission: {len(extra_task_ids)}")
        print("Extra task_ids:")
        for task_id in sorted(extra_task_ids):
            print(f"  - {task_id}")
