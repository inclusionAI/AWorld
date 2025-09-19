#!/usr/bin/env python3
"""
Dataset Usage Example
Demonstrates how to load local CSV files and showcase various Dataset functionalities
"""

import os
from aworld.dataset.dataset import Dataset
from aworld.dataset.dataloader import DataLoader
from aworld.dataset.sampler import SequentialSampler, RandomSampler, BatchSampler
from typing import Dict, Any


def test_load_hf_dataset(name: str, split: str = None):
    # Demonstrate loading dataset from Hugging Face
    print("=== Loading Dataset from Hugging Face ===")

    try:
        source = name
        split = split or "train"
        print(f"Loading '{source}' dataset from Hugging Face...")
        hf_dataset = Dataset[Dict[str, Any]](
            name=source.split("/")[1] + "_dataset",
            data=[]
        )

        # Load Hugging Face dataset
        hf_dataset.load_from(
            source=source,
            split=split,
            limit=5  # Limit to first 5 records for demonstration
        )

        print(f"Dataset name: {hf_dataset.name}")
        print(f"Dataset ID: {hf_dataset.id}")
        print(f"Number of records: {len(hf_dataset.data)}")
        print(f"Metadata: {hf_dataset.metadata}")

        if len(hf_dataset.data) > 0:
            print("\nFirst data item preview:")
            first_item = hf_dataset[0]
            if isinstance(first_item, dict):
                for key, value in list(first_item.items())[:3]:  # Only show first 3 fields
                    print(f"  {key}: {str(value)[:100]}{'...' if len(str(value)) > 100 else ''}")

        # Demonstrate Hugging Face dataset batch processing
        print("\nHugging Face dataset batch processing demo (batch_size=2):")
        batch_count = 0
        for batch in hf_dataset.to_dataloader(batch_size=2, shuffle=False):
            batch_count += 1
            print(f"  Batch {batch_count}: {len(batch)} samples")
            if batch_count >= 2:
                break

    except Exception as e:
        print(f"Error loading dataset from Hugging Face: {e}")
        print("This is usually because the 'datasets' library is missing, please run: pip install datasets")


def main():
    # 1. Load local CSV file
    print("=== 1. Loading Local CSV File ===")
    csv_path = os.path.expanduser("~/Downloads/hotpot_qa_validation.csv")

    # Check if file exists
    if not os.path.exists(csv_path):
        print(f"File does not exist: {csv_path}")
        print("Please ensure the file path is correct, or use another CSV file for testing")
        return

    # Create dataset and load data
    hotpot_qa_dataset = Dataset[Dict[str, Any]](
        name="hotpot_qa_dataset",
        data=[]  # Initially empty, will be populated via load_from
    )

    # Load CSV data
    hotpot_qa_dataset.load_from(
        source=csv_path,
        format="csv",
        limit=10  # Limit to first 10 records for demonstration
    )

    print(f"Dataset name: {hotpot_qa_dataset.name}")
    print(f"Dataset ID: {hotpot_qa_dataset.id}")
    print(f"Number of records: {len(hotpot_qa_dataset.data)}")
    print(f"Metadata: {hotpot_qa_dataset.metadata}")
    print()

    print("     === Loading Local CSV File with Transformation ===")
    # Load CSV data with transformation
    hotpot_qa_dataset_transform = Dataset[Dict[str, Any]](
        name="hotpot_qa_dataset_transform",
        data=[]  # Initially empty, will be populated via load_from
    )

    # Define a simple transformation function
    def rewrite_type(item: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(item, dict) and item.get("type"):
            item["type"] = "1_" + item["type"] if item["type"] == "comparison" else "2_" + item["type"]
        return item

    hotpot_qa_dataset_transform.load_from(
        source=csv_path,
        format="csv",
        preload_transform=rewrite_type,
        limit=10  # Limit to first 10 records for demonstration
    )

    print("     Original data vs Transformed data:")
    if len(hotpot_qa_dataset.data) > 0:
        original = hotpot_qa_dataset[0]
        transformed = hotpot_qa_dataset_transform[0]
        print(f"       Original field count: {len(original) if isinstance(original, dict) else 'N/A'}")
        print(f"       Transformed field count: {len(transformed) if isinstance(transformed, dict) else 'N/A'}")
        if isinstance(transformed, dict) and "type" in transformed:
            print(f"       origin = {original['type']}, origin = {transformed['type']}")
    print()

    # 2. Demonstrate basic data access functionality
    print("=== 2. Basic Data Access ===")
    if len(hotpot_qa_dataset.data) > 0:
        print("First data item:")
        first_item = hotpot_qa_dataset[0]
        print(f"  Type: {type(first_item)}")
        if isinstance(first_item, dict):
            print(f"  Fields: {list(first_item.keys())}")
            # Display content of first few fields
            for key, value in list(first_item.items())[:3]:
                print(f"  {key}: {str(value)[:100]}{'...' if len(str(value)) > 100 else ''}")
        print()

    # 3. Demonstrate different samplers
    print("=== 3. Different Samplers Demo ===")

    # Sequential sampler
    print("Sequential sampler (first 5 indices):")
    seq_sampler = SequentialSampler(len(hotpot_qa_dataset.data))
    for i, idx in enumerate(seq_sampler):
        if i >= 5:
            break
        print(f"  Index {i}: {idx}")
    print()

    # Random sampler
    print("Random sampler (first 5 indices, seed=42):")
    rand_sampler = RandomSampler(len(hotpot_qa_dataset.data), seed=42)
    for i, idx in enumerate(rand_sampler):
        if i >= 5:
            break
        print(f"  Index {i}: {idx}")
    print()

    # 4. Demonstrate batch processing functionality
    print("=== 4. Batch Processing Demo ===")

    # Use built-in to_dataloader method
    print("Using to_dataloader (batch_size=3, shuffle=True):")
    batch_count = 0
    for batch in hotpot_qa_dataset.to_dataloader(batch_size=3, shuffle=True, seed=123):
        batch_count += 1
        sample_ids = [b.get('id', 'N/A') if isinstance(b, dict) else str(b) for b in batch[:3]]
        print(f"  Batch {batch_count}|{len(batch)} samples: {', '.join(sample_ids)}")
        if batch_count >= 3:  # Only show first 3 batches
            break
    print()

    # Use standalone DataLoader class (recommended)
    print("Using standalone DataLoader (batch_size=3, shuffle=True):")
    batch_count = 0
    for batch in DataLoader(hotpot_qa_dataset, batch_size=3, shuffle=True, seed=123):
        batch_count += 1
        sample_ids = [b.get('id', 'N/A') if isinstance(b, dict) else str(b) for b in batch[:3]]
        print(f"  Batch {batch_count}|{len(batch)} samples: {', '.join(sample_ids)}")
        if batch_count >= 3:
            break
    print()

    # 5. Demonstrate custom sampler batch processing
    print("=== 5. Custom Batch Processing Sampler ===")
    base_sampler = RandomSampler(len(hotpot_qa_dataset.data), seed=456)
    batch_sampler = BatchSampler(base_sampler, batch_size=2, drop_last=False)

    print("Using BatchSampler (batch_size=2):")
    batch_count = 0
    for batch_indices in batch_sampler:
        batch_count += 1
        print(f"  Batch {batch_count} indices: {batch_indices}")
        if batch_count >= 3:
            break
    print()

    # 6. Demonstrate data transformation functionality
    print("=== 6. Data Transformation ===")

    # Define a simple transformation function
    def add_prefix(item: Dict[str, Any]) -> Dict[str, Any]:
        """Add prefix to each sample"""
        if isinstance(item, dict):
            return {"prefix": "sample", **item}
        return item

    # Create dataset with transformation
    transformed_dataset = Dataset[Dict[str, Any]](
        name="transformed_hotpot_qa",
        data=hotpot_qa_dataset.data.copy(),
        transforms=[add_prefix]
    )

    print("Original data vs Transformed data:")
    if len(hotpot_qa_dataset.data) > 0:
        original = hotpot_qa_dataset[0]
        transformed = transformed_dataset[0]
        print(f"  Original field count: {len(original) if isinstance(original, dict) else 'N/A'}")
        print(f"  Transformed field count: {len(transformed) if isinstance(transformed, dict) else 'N/A'}")
        if isinstance(transformed, dict) and "prefix" in transformed:
            print(f"  New field: prefix = {transformed['prefix']}")
    print()

    # 7. Demonstrate metadata functionality
    print("=== 7. Metadata Management ===")
    hotpot_qa_dataset.metadata.update({
        "description": "HotpotQA validation set",
        "version": "1.0",
        "created_by": "dataset_example.py"
    })
    print("Updated metadata:")
    for key, value in hotpot_qa_dataset.metadata.items():
        print(f"  {key}: {value}")
    print()

    # 8. Demonstrate dataset length and iteration
    print("=== 8. Dataset Length and Iteration ===")
    print(f"Dataset length: {len(hotpot_qa_dataset.data)}")
    print("Index access for first 3 samples:")
    for i in range(min(3, len(hotpot_qa_dataset.data))):
        item = hotpot_qa_dataset[i]
        if isinstance(item, dict):
            # Display first and sceond field value as identifier
            first_key = list(item.keys())[0] if item else "empty"
            first_value = str(item[first_key])[:50] if item else "empty"
            second_key = list(item.keys())[1] if item else "empty"
            second_value = str(item[second_key])[:50] if item else "empty"
            print(f"  Sample {i}: {first_key} = {first_value}, {second_key} = {second_value}...")

    print()

    print("=== Example Complete ===")
    print("This example demonstrates the main features of the Dataset class:")
    print("- Loading data from local CSV files")
    print("- Basic data access and indexing")
    print("- Different samplers (sequential, random)")
    print("- Batch processing functionality")
    print("- Data transformation")
    print("- Metadata management")
    print("- Dataset length and iteration")


if __name__ == "__main__":
    main()
