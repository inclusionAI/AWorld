import json
import random
from collections import Counter
from pathlib import Path

if __name__ == "__main__":

    data_dir = Path("/Users/arac/Desktop/AWorld/gaia-benchmark/GAIA/2023/test")

    dataset = []
    with open(data_dir / "metadata.jsonl", "r", encoding="utf-8") as metaf:
        lines = metaf.readlines()
        for line in lines:
            data = json.loads(line)
            if data["task_id"] == "0-0-0-0-0":
                continue
            if data["file_name"]:
                data["file_name"] = data_dir / data["file_name"]
            dataset.append(line)

    # Shuffle the dataset
    random.shuffle(dataset)

    # Randomly sample 60 entries
    sampled_data = random.sample(dataset, 60)

    # Extract levels and count occurrences
    levels = [json.loads(entry)["Level"] for entry in sampled_data]
    level_counts = Counter(levels)

    # Calculate proportions
    total_samples = len(sampled_data)
    level_proportions = {
        level: count / total_samples for level, count in level_counts.items()
    }

    # Output the level proportions
    print("Level Proportions:", level_proportions)

    with open(
        data_dir / "sample_metadata.jsonl",
        "w",
        encoding="utf-8",
    ) as f:
        for entry in sampled_data:
            f.writelines(entry)
