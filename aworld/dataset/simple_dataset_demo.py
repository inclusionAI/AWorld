#!/usr/bin/env python3
"""
简化的 Dataset 演示
创建一个示例 CSV 文件并展示 Dataset 的功能
"""

import os
import csv
import tempfile
from simplified_dataset import Dataset, SequentialSampler, RandomSampler, BatchSampler
from typing import Dict, Any

def create_sample_csv():
    """创建一个示例 CSV 文件用于演示"""
    # 创建临时 CSV 文件
    temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False)
    
    # 写入示例数据
    fieldnames = ['question', 'answer', 'context', 'type', 'level']
    writer = csv.DictWriter(temp_file, fieldnames=fieldnames)
    writer.writeheader()
    
    sample_data = [
        {
            'question': 'What is the capital of France?',
            'answer': 'Paris',
            'context': 'France is a country in Europe. Its capital city is Paris.',
            'type': 'factual',
            'level': 'easy'
        },
        {
            'question': 'Who wrote Romeo and Juliet?',
            'answer': 'William Shakespeare',
            'context': 'Romeo and Juliet is a tragedy written by William Shakespeare.',
            'type': 'literature',
            'level': 'medium'
        },
        {
            'question': 'What is the chemical symbol for gold?',
            'answer': 'Au',
            'context': 'Gold is a chemical element with the symbol Au and atomic number 79.',
            'type': 'science',
            'level': 'easy'
        },
        {
            'question': 'In which year did World War II end?',
            'answer': '1945',
            'context': 'World War II ended in 1945 with the surrender of Japan.',
            'type': 'history',
            'level': 'medium'
        },
        {
            'question': 'What is the largest planet in our solar system?',
            'answer': 'Jupiter',
            'context': 'Jupiter is the largest planet in our solar system.',
            'type': 'science',
            'level': 'easy'
        }
    ]
    
    writer.writerows(sample_data)
    temp_file.close()
    return temp_file.name

def main():
    print("=== Dataset 功能演示 ===\n")
    
    # 1. 创建示例 CSV 文件
    print("1. 创建示例 CSV 文件...")
    csv_path = create_sample_csv()
    print(f"   创建文件: {csv_path}\n")
    
    # 2. 加载数据集
    print("2. 加载数据集...")
    dataset = Dataset[Dict[str, Any]](
        name="sample_qa_dataset",
        data=[]
    )
    
    dataset.load_from(
        source=csv_path,
        format="csv"
    )
    
    print(f"   数据集名称: {dataset.name}")
    print(f"   数据集ID: {dataset.id}")
    print(f"   数据条数: {len(dataset.data)}")
    print(f"   元数据: {dataset.metadata}\n")
    
    # 3. 展示数据访问
    print("3. 数据访问演示...")
    print("   第一条数据:")
    first_item = dataset[0]
    if isinstance(first_item, dict):
        for key, value in first_item.items():
            print(f"     {key}: {value}")
    print()
    
    # 4. 展示采样器
    print("4. 采样器演示...")
    
    # 顺序采样器
    print("   顺序采样器 (前3个索引):")
    seq_sampler = SequentialSampler(len(dataset.data))
    for i, idx in enumerate(seq_sampler):
        if i >= 3:
            break
        print(f"     索引 {i}: {idx}")
    
    # 随机采样器
    print("   随机采样器 (前3个索引，种子=42):")
    rand_sampler = RandomSampler(len(dataset.data), seed=42)
    for i, idx in enumerate(rand_sampler):
        if i >= 3:
            break
        print(f"     索引 {i}: {idx}")
    print()
    
    # 5. 展示批处理
    print("5. 批处理演示...")
    print("   使用 to_dataloader (batch_size=2, shuffle=True):")
    batch_count = 0
    for batch in dataset.to_dataloader(batch_size=2, shuffle=True, seed=123):
        batch_count += 1
        print(f"     批次 {batch_count}: {len(batch)} 个样本")
        for i, item in enumerate(batch):
            if isinstance(item, dict) and 'question' in item:
                print(f"       样本 {i+1}: {item['question'][:50]}...")
        if batch_count >= 2:
            break
    print()
    
    # 6. 展示数据变换
    print("6. 数据变换演示...")
    
    def add_metadata(item: Dict[str, Any]) -> Dict[str, Any]:
        """为每个样本添加元数据"""
        if isinstance(item, dict):
            return {
                **item,
                "sample_id": f"sample_{hash(item.get('question', '')) % 1000:03d}",
                "processed": True
            }
        return item
    
    # 创建带变换的数据集
    transformed_dataset = Dataset[Dict[str, Any]](
        name="transformed_dataset",
        data=dataset.data.copy(),
        transform=add_metadata
    )
    
    print("   变换后的第一条数据:")
    transformed_item = transformed_dataset[0]
    if isinstance(transformed_item, dict):
        for key, value in transformed_item.items():
            print(f"     {key}: {value}")
    print()
    
    # 7. 展示 BatchSampler
    print("7. BatchSampler 演示...")
    base_sampler = RandomSampler(len(dataset.data), seed=456)
    batch_sampler = BatchSampler(base_sampler, batch_size=2, drop_last=False)
    
    print("   使用 BatchSampler (batch_size=2):")
    batch_count = 0
    for batch_indices in batch_sampler:
        batch_count += 1
        print(f"     批次 {batch_count} 索引: {batch_indices}")
        if batch_count >= 2:
            break
    print()
    
    # 8. 清理临时文件
    print("8. 清理临时文件...")
    os.unlink(csv_path)
    print(f"   已删除: {csv_path}\n")
    
    # 9. 展示从 Hugging Face 加载数据集
    print("9. 从 Hugging Face 加载数据集演示...")
    
    try:
        print("   正在从 Hugging Face 加载 'LucasFang/FLUX-Reason-6M' 数据集...")
        flux_dataset = Dataset[Dict[str, Any]](
            name="flux_reason_dataset",
            data=[]
        )
        
        # 加载 Hugging Face 数据集
        flux_dataset.load_from(
            source="LucasFang/FLUX-Reason-6M",
            split="train",
            limit=3  # 限制加载前3条记录用于演示
        )
        
        print(f"   数据集名称: {flux_dataset.name}")
        print(f"   数据条数: {len(flux_dataset.data)}")
        print(f"   元数据: {flux_dataset.metadata}")
        
        if len(flux_dataset.data) > 0:
            print("   第一条数据预览:")
            first_item = flux_dataset[0]
            if isinstance(first_item, dict):
                for key, value in list(first_item.items())[:2]:  # 只显示前2个字段
                    print(f"     {key}: {str(value)[:80]}{'...' if len(str(value)) > 80 else ''}")
        
        # 展示批处理
        print("   Hugging Face 数据集批处理演示:")
        for i, batch in enumerate(flux_dataset.to_dataloader(batch_size=2, shuffle=False)):
            print(f"     批次 {i+1}: {len(batch)} 个样本")
            if i >= 1:  # 只显示前2个批次
                break
                
    except Exception as e:
        print(f"   从 Hugging Face 加载数据集时出错: {e}")
        print("   这通常是因为缺少 'datasets' 库，请运行: pip install datasets")
    
    print()
    
    print("=== 演示完成 ===")
    print("Dataset 类的主要功能:")
    print("✓ 从本地 CSV 文件加载数据")
    print("✓ 从 Hugging Face Hub 加载数据集")
    print("✓ 基本的数据访问和索引")
    print("✓ 顺序和随机采样器")
    print("✓ 批处理功能 (to_dataloader)")
    print("✓ 自定义批处理采样器 (BatchSampler)")
    print("✓ 数据变换功能")
    print("✓ 元数据管理")
    print("✓ 数据集长度和迭代")

if __name__ == "__main__":
    main()
