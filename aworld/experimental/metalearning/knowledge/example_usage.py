# coding: utf-8
"""
base_coder.py 完整工作流程示例

演示：解析目录 -> 生成补丁 -> 复制目录 -> 应用补丁的完整流程
"""

import asyncio
import os
import tempfile
from pathlib import Path
from base_coder import base_coder, DirectoryCodeProcessor


async def create_sample_project():
    """创建示例项目用于测试 - 此函数不再使用，保留以防需要"""
    # 此函数现在不再使用，因为我们直接使用真实目录
    # 保留此函数定义以防其他地方引用
    pass


async def show_diff_examples(source_dir: str, target_dir: str, patches):
    """显示修改前后的代码对比"""
    for patch in patches:
        print(f"📝 文件对比: {patch.file_path}")
        print("   " + "="*60)

        # 读取修改前后的文件内容
        source_file = Path(source_dir) / patch.file_path
        target_file = Path(target_dir) / patch.file_path

        try:
            original_content = source_file.read_text().strip()
            modified_content = target_file.read_text().strip()

            print("   修改前 (前10行):")
            for i, line in enumerate(original_content.split('\n')[:10]):
                print(f"     {i+1:2d}| {line}")

            print("\n   修改后 (前10行):")
            for i, line in enumerate(modified_content.split('\n')[:10]):
                print(f"     {i+1:2d}| {line}")

            print(f"\n   📈 修改统计:")
            print(f"      原始行数: {len(original_content.splitlines())}")
            print(f"      修改后行数: {len(modified_content.splitlines())}")

        except Exception as e:
            print(f"   ❌ 无法读取文件内容: {e}")

        print()


async def demonstrate_full_workflow():
    """演示完整的工作流程"""
    print("=== BaseCoder 完整工作流程示例 ===\n")

    # 设置真实的源目录和目标目录
    source_project = "/Users/hgc/.aworld/agents/doc_code_agent"
    target_project = "/Users/hgc/.aworld/agents/doc_code_agent_v1"

    try:
        # 1. 检查源目录是否存在
        print("🔍 步骤1: 检查源目录...")
        source_path = Path(source_project)
        if not source_path.exists():
            print(f"   ❌ 源目录不存在: {source_project}")
            print("   请确保目录路径正确")
            return

        print(f"   ✅ 源目录存在: {source_project}")
        print(f"   📂 目标目录将创建为: {target_project}\n")

        # 2. 解析目录下的所有代码
        print("📖 步骤2: 解析目录下的代码文件...")
        processor = DirectoryCodeProcessor(base_coder)

        parse_results = await processor.parse_directory(source_project)

        if parse_results["success"]:
            summary = parse_results["summary"]
            print(f"   ✅ 解析成功")
            print(f"   📁 总文件数: {summary['total_files']}")
            print(f"   ✅ 成功解析: {summary['parsed_files']}")
            print(f"   🧩 代码元素总数: {summary['total_elements']}")

            if summary["errors"]:
                print(f"   ❌ 错误: {len(summary['errors'])}")
                for error in summary["errors"][:3]:  # 只显示前3个错误
                    print(f"      {error}")

            # 显示解析的文件详情
            print(f"\n   📄 解析的文件:")
            for file_path in parse_results["files"].keys():
                print(f"      - {file_path}")
        else:
            print("   ❌ 解析失败")
            return

        print()

        # 3. 生成代码修改补丁
        print("🔄 步骤3: 生成代码修改补丁...")
        modification_intent = "add docstring and type hints"  # 修改意图：添加文档字符串和类型提示

        patches = await processor.generate_code_patches(parse_results, modification_intent)

        print(f"   ✅ 生成了 {len(patches)} 个补丁")
        for i, patch in enumerate(patches):
            print(f"   📄 补丁 {i+1}: {patch.file_path}")
            print(f"      意图: {patch.metadata.get('modification_intent', 'N/A')}")
        print()

        # 4. 复制原目录到新位置
        print("📂 步骤4: 复制原目录到新位置...")

        copy_success = processor.copy_directory(source_project, target_project)
        if copy_success:
            print(f"   ✅ 成功复制到: {target_project}")
        else:
            print("   ❌ 复制失败")
            return
        print()

        # 5. 应用补丁到新目录
        print("🔨 步骤5: 应用补丁到新目录...")
        apply_results = processor.apply_patches(patches, target_project)

        if apply_results["success"]:
            print(f"   ✅ 成功应用 {apply_results['applied_patches']} 个补丁")
            if apply_results["failed_patches"] > 0:
                print(f"   ⚠️  失败 {apply_results['failed_patches']} 个补丁")
        else:
            print(f"   ❌ 应用补丁失败")
            for error in apply_results["errors"]:
                print(f"      {error}")
        print()

        # 6. 展示修改前后的对比
        print("📊 步骤6: 展示修改前后的对比...")
        await show_diff_examples(source_project, target_project, patches[:2])  # 只显示前2个文件的对比

        print(f"\n🎉 完整工作流程演示完成!")
        print(f"📁 原始目录: {source_project}")
        print(f"📁 修改后目录: {target_project}")

    except Exception as e:
        print(f"❌ 工作流程执行失败: {str(e)}")
        import traceback
        traceback.print_exc()


async def analyze_target_directory():
    """单独分析目标目录功能"""
    print("=== 目标目录分析示例 ===\n")

    target_directory = "/Users/hgc/.aworld/agents/doc_code_agent"
    processor = DirectoryCodeProcessor(base_coder)

    # 检查目录是否存在
    target_path = Path(target_directory)
    if not target_path.exists():
        print(f"❌ 目录不存在: {target_directory}")
        print("请确保目录路径正确")
        return

    print(f"分析目录: {target_directory}")

    results = await processor.parse_directory(target_directory)

    if results["success"]:
        print("\n📊 分析结果汇总:")
        summary = results["summary"]
        print(f"   总文件数: {summary['total_files']}")
        print(f"   解析成功: {summary['parsed_files']}")
        print(f"   代码元素: {summary['total_elements']}")

        if summary["errors"]:
            print(f"   错误数量: {len(summary['errors'])}")

        print("\n📂 文件详情:")
        for file_path, file_data in results["files"].items():
            if file_data["analysis"].success:
                insights = file_data["analysis"].insights
                print(f"   📄 {file_path}:")
                print(f"      元素数量: {len(file_data['parse'].elements)}")
                if "functions" in insights:
                    print(f"      函数数量: {insights['functions']['count']}")
                if "classes" in insights:
                    print(f"      类数量: {insights['classes']['count']}")

                # 显示改进建议
                if file_data["analysis"].suggestions:
                    print(f"      建议:")
                    for suggestion in file_data["analysis"].suggestions[:2]:  # 只显示前2个建议
                        print(f"        - {suggestion}")
    else:
        print("❌ 分析失败")
        for error in results["summary"]["errors"]:
            print(f"   {error}")


async def quick_patch_generation():
    """快速补丁生成 - 不复制目录，只生成补丁"""
    print("=== 快速补丁生成模式 ===\n")

    source_directory = "/Users/hgc/.aworld/agents/doc_code_agent"
    processor = DirectoryCodeProcessor(base_coder)

    # 检查目录是否存在
    source_path = Path(source_directory)
    if not source_path.exists():
        print(f"❌ 目录不存在: {source_directory}")
        return

    print(f"分析目录: {source_directory}")

    # 解析目录
    parse_results = await processor.parse_directory(source_directory)
    if not parse_results["success"]:
        print("❌ 解析失败")
        return

    print(f"✅ 解析成功，找到 {parse_results['summary']['parsed_files']} 个文件")

    # 生成补丁
    print("\n生成补丁中...")
    patches = await processor.generate_code_patches(parse_results, "add docstring and type hints")

    print(f"✅ 生成了 {len(patches)} 个补丁\n")

    # 显示每个补丁的统一diff格式
    for i, patch in enumerate(patches):
        print(f"📄 补丁 {i+1}: {patch.file_path}")
        print("="*60)
        print(patch.patch_content[:500])  # 只显示前500字符
        if len(patch.patch_content) > 500:
            print("... (补丁内容已截断)")
        print()


if __name__ == "__main__":
    print("选择运行模式:")
    print("1. 完整工作流程演示 (解析 -> 补丁 -> 复制 -> 应用)")
    print("2. 目标目录分析")
    print("3. 快速补丁生成 (仅生成补丁，不应用)")

    choice = input("\n请输入选择 (1/2/3): ").strip()

    if choice == "2":
        asyncio.run(analyze_target_directory())
    elif choice == "3":
        asyncio.run(quick_patch_generation())
    else:
        asyncio.run(demonstrate_full_workflow())