from aworld.evaluations.base import Dataset
import csv
import os


def load_dataset_from_csv(dataset_path: str) -> Dataset:
    """Load dataset from csv file.

    Args:
        dataset_path: The path of dataset file.

    Returns:
        Dataset.
    """
    with open(dataset_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        data = [row for row in reader]
    return Dataset(rows=data)
