# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""
AWorld Dataset Module

This module provides a comprehensive dataset management system for AWorld,
including various dataset implementations, loaders, and utilities.

Key Components:
- Dataset: Abstract base class for all datasets
- MemoryDataset: In-memory dataset implementation
- FileDataset: File-based dataset implementation
- StreamingFileDataset: Streaming dataset for large files
- DatasetLoader: Various data loaders (JSON, CSV, HuggingFace, etc.)
- DatasetUtils: Analysis, validation, and manipulation utilities

Example Usage:
    >>> from aworld.dataset import MemoryDataset, load_from_file
    >>> 
    >>> # Create a memory dataset
    >>> dataset = MemoryDataset("my_dataset")
    >>> dataset.add_item("Hello, World!")
    >>> 
    >>> # Load from file
    >>> dataset = load_from_file("data.json", "json")
    >>> 
    >>> # Analyze dataset
    >>> from aworld.dataset.utils import get_dataset_info
    >>> info = get_dataset_info(dataset)
"""

from aworld.dataset.base import (
    Dataset,
    DatasetItem,
    DatasetMetadata,
    DatasetFilter,
    MetadataFilter,
    DataTypeFilter,
    FilteredDataset,
    MappedDataset,
    BatchedDataset,
    SubsetDataset,
    SampledDataset,
    ShuffledDataset
)

from aworld.dataset.memory_dataset import MemoryDataset
from aworld.dataset.file_dataset import FileDataset, StreamingFileDataset
from aworld.dataset.loaders import (
    DatasetLoader,
    JSONLoader,
    JSONLLoader,
    CSVLoader,
    HuggingFaceLoader,
    WebLoader,
    DatabaseLoader,
    DatasetBuilder,
    DatasetRegistry,
    DatasetPreprocessor,
    TextPreprocessor,
    ImagePreprocessor,
    load_dataset,
    create_dataset,
    load_from_file,
    load_from_huggingface,
    load_from_web,
    load_from_database,
    registry
)

from aworld.dataset.utils import (
    DatasetStats,
    DatasetAnalyzer,
    DatasetValidator,
    DatasetComparator,
    DatasetMerger,
    DatasetSampler,
    DatasetSplitter,
    calculate_dataset_hash,
    validate_dataset_integrity,
    get_dataset_info,
    export_dataset,
    import_dataset
)

__all__ = [
    # Base classes
    "Dataset",
    "DatasetItem", 
    "DatasetMetadata",
    "DatasetFilter",
    "MetadataFilter",
    "DataTypeFilter",
    
    # Dataset implementations
    "MemoryDataset",
    "FileDataset",
    "StreamingFileDataset",
    
    # View classes
    "FilteredDataset",
    "MappedDataset",
    "BatchedDataset",
    "SubsetDataset",
    "SampledDataset",
    "ShuffledDataset",
    
    # Loaders
    "DatasetLoader",
    "JSONLoader",
    "JSONLLoader", 
    "CSVLoader",
    "HuggingFaceLoader",
    "WebLoader",
    "DatabaseLoader",
    "DatasetBuilder",
    "DatasetRegistry",
    "DatasetPreprocessor",
    "TextPreprocessor",
    "ImagePreprocessor",
    
    # Utility functions
    "load_dataset",
    "create_dataset",
    "load_from_file",
    "load_from_huggingface",
    "load_from_web",
    "load_from_database",
    "registry",
    
    # Analysis and utilities
    "DatasetStats",
    "DatasetAnalyzer",
    "DatasetValidator",
    "DatasetComparator",
    "DatasetMerger",
    "DatasetSampler",
    "DatasetSplitter",
    "calculate_dataset_hash",
    "validate_dataset_integrity",
    "get_dataset_info",
    "export_dataset",
    "import_dataset"
]
