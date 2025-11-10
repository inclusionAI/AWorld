#!/usr/bin/env python3
"""
Base64 图片解码脚本
用于将存储了 base64 编码图片数据的文件解码为实际的图片文件
"""

import base64
import sys
import os
import argparse
from pathlib import Path


def decode_base64_image(input_file: str, output_file: str = None):
    """
    从文件中读取 base64 编码的图片数据，解码并保存为图片文件
    
    Args:
        input_file: 包含 base64 数据的输入文件路径
        output_file: 输出图片文件路径（可选，默认使用输入文件名但扩展名为 .png）
    """
    # 检查输入文件是否存在
    if not os.path.exists(input_file):
        print(f"错误: 文件 '{input_file}' 不存在")
        return False
    
    # 读取文件内容
    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            base64_data = f.read().strip()
    except Exception as e:
        print(f"错误: 读取文件失败 - {e}")
        return False
    
    # 处理 data URI 格式（如 data:image/png;base64,...）
    if base64_data.startswith('data:'):
        # 提取 base64 部分
        if ',' in base64_data:
            base64_data = base64_data.split(',', 1)[1]
        else:
            print("错误: 无效的 data URI 格式")
            return False
    
    # 解码 base64 数据
    try:
        image_data = base64.b64decode(base64_data)
    except Exception as e:
        print(f"错误: Base64 解码失败 - {e}")
        return False
    
    # 确定输出文件名
    if output_file is None:
        input_path = Path(input_file)
        output_file = input_path.parent / f"{input_path.stem}_decoded.png"
    
    # 保存图片文件
    try:
        with open(output_file, 'wb') as f:
            f.write(image_data)
        print(f"成功: 图片已保存到 '{output_file}'")
        print(f"文件大小: {len(image_data)} 字节")
        return True
    except Exception as e:
        print(f"错误: 保存文件失败 - {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description='将 base64 编码的图片数据解码为图片文件',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 解码 a.png 文件中的 base64 数据，输出为 a_decoded.png
  python decode_base64_image.py a.png
  
  # 指定输出文件名
  python decode_base64_image.py a.png -o output.png
        """
    )
    parser.add_argument('input_file', help='包含 base64 数据的输入文件')
    parser.add_argument('-o', '--output', dest='output_file', 
                       help='输出图片文件路径（默认: 输入文件名_decoded.png）')
    
    args = parser.parse_args()
    
    success = decode_base64_image(args.input_file, args.output_file)
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()

