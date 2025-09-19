#!/usr/bin/env python3
"""
Hugging Face 数据集加载示例
专门演示如何从 Hugging Face Hub 加载 "LucasFang/FLUX-Reason-6M" 数据集
"""

from simplified_dataset import Dataset, RandomSampler, BatchSampler
from typing import Dict, Any

def main():
    print("=== Hugging Face 数据集加载示例 ===\n")
    
    # 1. 加载 FLUX-Reason-6M 数据集
    print("1. 加载 'LucasFang/FLUX-Reason-6M' 数据集...")
    
    try:
        flux_dataset = Dataset[Dict[str, Any]](
            name="flux_reason_6m",
            data=[]
        )
        
        # 加载数据集
        flux_dataset.load_from(
            source="LucasFang/FLUX-Reason-6M",
            split="train",  # 使用训练集
            limit=10  # 限制加载前10条记录用于演示
        )
        
        print(f"   ✓ 数据集名称: {flux_dataset.name}")
        print(f"   ✓ 数据集ID: {flux_dataset.id}")
        print(f"   ✓ 数据条数: {len(flux_dataset.data)}")
        print(f"   ✓ 元数据: {flux_dataset.metadata}")
        print()
        
    except Exception as e:
        print(f"   ✗ 加载失败: {e}")
        print("   请确保已安装 datasets 库: pip install datasets")
        return
    
    # 2. 探索数据集结构
    print("2. 探索数据集结构...")
    if len(flux_dataset.data) > 0:
        first_item = flux_dataset[0]
        print(f"   第一条数据的类型: {type(first_item)}")
        if isinstance(first_item, dict):
            print(f"   字段列表: {list(first_item.keys())}")
            print("   前3个字段的内容预览:")
            for i, (key, value) in enumerate(first_item.items()):
                if i >= 3:
                    break
                value_str = str(value)
                if len(value_str) > 100:
                    value_str = value_str[:100] + "..."
                print(f"     {key}: {value_str}")
        print()
    
    # 3. 展示不同的数据访问方式
    print("3. 数据访问演示...")
    print("   顺序访问前3个样本:")
    for i in range(min(3, len(flux_dataset.data))):
        item = flux_dataset[i]
        if isinstance(item, dict):
            # 尝试获取常见的字段
            title = item.get('title', item.get('question', item.get('text', 'N/A')))
            if isinstance(title, str) and len(title) > 50:
                title = title[:50] + "..."
            print(f"     样本 {i}: {title}")
    print()
    
    # 4. 展示随机采样
    print("4. 随机采样演示...")
    rand_sampler = RandomSampler(len(flux_dataset.data), seed=42)
    print("   随机采样前5个索引:")
    for i, idx in enumerate(rand_sampler):
        if i >= 5:
            break
        print(f"     索引 {i}: {idx}")
    print()
    
    # 5. 展示批处理
    print("5. 批处理演示...")
    print("   使用 to_dataloader (batch_size=3, shuffle=True):")
    batch_count = 0
    for batch in flux_dataset.to_dataloader(batch_size=3, shuffle=True, seed=123):
        batch_count += 1
        print(f"     批次 {batch_count}: {len(batch)} 个样本")
        if batch_count >= 3:
            break
    print()
    
    # 6. 展示自定义批处理采样器
    print("6. 自定义批处理采样器演示...")
    base_sampler = RandomSampler(len(flux_dataset.data), seed=456)
    batch_sampler = BatchSampler(base_sampler, batch_size=2, drop_last=False)
    
    print("   使用 BatchSampler (batch_size=2):")
    batch_count = 0
    for batch_indices in batch_sampler:
        batch_count += 1
        print(f"     批次 {batch_count} 索引: {batch_indices}")
        if batch_count >= 3:
            break
    print()
    
    # 7. 展示数据变换
    print("7. 数据变换演示...")
    
    def add_processing_info(item: Dict[str, Any]) -> Dict[str, Any]:
        """为每个样本添加处理信息"""
        if isinstance(item, dict):
            return {
                **item,
                "processed_by": "huggingface_dataset_example.py",
                "sample_type": "flux_reason",
                "processing_timestamp": "2024-01-01T00:00:00Z"
            }
        return item
    
    # 创建带变换的数据集
    transformed_dataset = Dataset[Dict[str, Any]](
        name="transformed_flux_reason",
        data=flux_dataset.data.copy(),
        transform=add_processing_info
    )
    
    print("   变换后的第一条数据字段:")
    transformed_item = transformed_dataset[0]
    if isinstance(transformed_item, dict):
        for key in list(transformed_item.keys())[:5]:  # 显示前5个字段
            print(f"     {key}")
    print()
    
    # 8. 展示数据集统计信息
    print("8. 数据集统计信息...")
    print(f"   总样本数: {len(flux_dataset.data)}")
    print(f"   数据集名称: {flux_dataset.name}")
    print(f"   数据集ID: {flux_dataset.id}")
    print(f"   元数据字段: {list(flux_dataset.metadata.keys())}")
    
    # 计算一些基本统计
    if len(flux_dataset.data) > 0 and isinstance(flux_dataset.data[0], dict):
        first_item = flux_dataset.data[0]
        print(f"   每个样本的字段数: {len(first_item)}")
        print(f"   字段列表: {list(first_item.keys())}")
    
    print()
    
    # 9. 展示不同分割的加载
    print("9. 尝试加载不同分割...")
    try:
        # 尝试加载验证集（如果存在）
        val_dataset = Dataset[Dict[str, Any]](
            name="flux_reason_validation",
            data=[]
        )
        
        val_dataset.load_from(
            source="LucasFang/FLUX-Reason-6M",
            split="validation",  # 尝试加载验证集
            limit=3
        )
        
        print(f"   ✓ 验证集加载成功: {len(val_dataset.data)} 个样本")
        
    except Exception as e:
        print(f"   ! 验证集加载失败: {e}")
        print("   这通常是因为该数据集没有验证集分割")
    
    print()
    
    print("=== 示例完成 ===")
    print("Hugging Face 数据集加载功能:")
    print("✓ 从 Hugging Face Hub 加载数据集")
    print("✓ 支持不同的数据集分割 (train/validation/test)")
    print("✓ 自动处理数据集元数据")
    print("✓ 支持数据限制 (limit 参数)")
    print("✓ 与所有 Dataset 功能兼容 (采样、批处理、变换等)")

if __name__ == "__main__":
    main()
