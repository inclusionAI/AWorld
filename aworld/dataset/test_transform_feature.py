#!/usr/bin/env python3
"""
测试 Dataset.load_from 函数的 transform 参数功能
"""

import tempfile
import json
import csv
from pathlib import Path
from dataset import Dataset

def test_csv_transform():
    """测试 CSV 文件的 transform 功能"""
    # 创建测试 CSV 文件
    test_data = [
        {"name": "Alice", "age": "25", "city": "New York"},
        {"name": "Bob", "age": "30", "city": "San Francisco"},
        {"name": "Charlie", "age": "35", "city": "Los Angeles"}
    ]
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        writer = csv.DictWriter(f, fieldnames=["name", "age", "city"])
        writer.writeheader()
        writer.writerows(test_data)
        csv_path = f.name
    
    try:
        # 定义 transform 函数：将 age 转换为整数
        def transform_person(person):
            person["age"] = int(person["age"])
            return person
        
        # 测试不使用 transform
        dataset1 = Dataset[str](name="test1", data=[])
        dataset1.load_from(csv_path)
        print("不使用 transform:")
        print(f"第一个人的年龄类型: {type(dataset1.data[0]['age'])}")
        print(f"第一个人的年龄值: {dataset1.data[0]['age']}")
        
        # 测试使用 transform
        dataset2 = Dataset[str](name="test2", data=[])
        dataset2.load_from(csv_path, transform=transform_person)
        print("\n使用 transform:")
        print(f"第一个人的年龄类型: {type(dataset2.data[0]['age'])}")
        print(f"第一个人的年龄值: {dataset2.data[0]['age']}")
        
        # 验证 transform 确实被应用了
        assert isinstance(dataset2.data[0]['age'], int), "年龄应该是整数"
        assert dataset2.data[0]['age'] == 25, "年龄值应该是 25"
        
        print("✅ CSV transform 测试通过!")
        
    finally:
        # 清理临时文件
        Path(csv_path).unlink()

def test_json_transform():
    """测试 JSON 文件的 transform 功能"""
    # 创建测试 JSON 文件
    test_data = [
        {"id": 1, "score": "85.5", "passed": "true"},
        {"id": 2, "score": "92.0", "passed": "false"},
        {"id": 3, "score": "78.3", "passed": "true"}
    ]
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(test_data, f)
        json_path = f.name
    
    try:
        # 定义 transform 函数：转换数据类型
        def transform_student(student):
            student["score"] = float(student["score"])
            student["passed"] = student["passed"] == "true"
            return student
        
        # 测试使用 transform
        dataset = Dataset[str](name="test", data=[])
        dataset.load_from(json_path, transform=transform_student)
        
        print("JSON transform 测试:")
        print(f"第一个学生的分数类型: {type(dataset.data[0]['score'])}")
        print(f"第一个学生的分数值: {dataset.data[0]['score']}")
        print(f"第一个学生的通过状态类型: {type(dataset.data[0]['passed'])}")
        print(f"第一个学生的通过状态值: {dataset.data[0]['passed']}")
        
        # 验证 transform 确实被应用了
        assert isinstance(dataset.data[0]['score'], float), "分数应该是浮点数"
        assert isinstance(dataset.data[0]['passed'], bool), "通过状态应该是布尔值"
        assert dataset.data[0]['score'] == 85.5, "分数值应该是 85.5"
        assert dataset.data[0]['passed'] == True, "通过状态应该是 True"
        
        print("✅ JSON transform 测试通过!")
        
    finally:
        # 清理临时文件
        Path(json_path).unlink()

def test_txt_transform():
    """测试 TXT 文件的 transform 功能"""
    # 创建测试 TXT 文件
    test_lines = ["hello world", "python programming", "machine learning"]
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        for line in test_lines:
            f.write(line + "\n")
        txt_path = f.name
    
    try:
        # 定义 transform 函数：转换为大写并添加前缀
        def transform_text(text):
            return f"TEXT: {text.upper()}"
        
        # 测试使用 transform
        dataset = Dataset[str](name="test", data=[])
        dataset.load_from(txt_path, transform=transform_text)
        
        print("TXT transform 测试:")
        print(f"第一行内容: {dataset.data[0]}")
        print(f"第二行内容: {dataset.data[1]}")
        
        # 验证 transform 确实被应用了
        assert dataset.data[0] == "TEXT: HELLO WORLD", "第一行应该被转换"
        assert dataset.data[1] == "TEXT: PYTHON PROGRAMMING", "第二行应该被转换"
        
        print("✅ TXT transform 测试通过!")
        
    finally:
        # 清理临时文件
        Path(txt_path).unlink()

if __name__ == "__main__":
    print("开始测试 Dataset.load_from 的 transform 功能...\n")
    
    test_csv_transform()
    print()
    test_json_transform()
    print()
    test_txt_transform()
    
    print("\n🎉 所有测试都通过了！transform 功能工作正常。")
