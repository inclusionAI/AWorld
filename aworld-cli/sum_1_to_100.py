#!/usr/bin/env python3
"""
计算1+2+3+...+100的和 - 多种实现方法
作者: DocCodeAgent
日期: 2024年
"""

import time
import functools
from typing import List, Callable


def method1_for_loop(n: int = 100) -> int:
    """
    方法1: 使用for循环实现
    
    时间复杂度: O(n)
    空间复杂度: O(1)
    
    Args:
        n: 计算从1到n的和，默认为100
        
    Returns:
        int: 计算结果
    """
    total = 0
    for i in range(1, n + 1):
        total += i
    return total


def method2_while_loop(n: int = 100) -> int:
    """
    方法2: 使用while循环实现
    
    时间复杂度: O(n)
    空间复杂度: O(1)
    
    Args:
        n: 计算从1到n的和，默认为100
        
    Returns:
        int: 计算结果
    """
    total = 0
    i = 1
    while i <= n:
        total += i
        i += 1
    return total


def method3_mathematical_formula(n: int = 100) -> int:
    """
    方法3: 使用数学公式 n*(n+1)/2
    
    这是最高效的方法，基于高斯求和公式
    时间复杂度: O(1)
    空间复杂度: O(1)
    
    Args:
        n: 计算从1到n的和，默认为100
        
    Returns:
        int: 计算结果
    """
    return n * (n + 1) // 2


def method4_recursion(n: int = 100) -> int:
    """
    方法4: 使用递归实现
    
    时间复杂度: O(n)
    空间复杂度: O(n) - 由于递归调用栈
    
    Args:
        n: 计算从1到n的和，默认为100
        
    Returns:
        int: 计算结果
    """
    if n <= 1:
        return n
    return n + method4_recursion(n - 1)


@functools.lru_cache(maxsize=None)
def method5_recursion_with_memoization(n: int = 100) -> int:
    """
    方法5: 使用带记忆化的递归实现
    
    通过缓存避免重复计算，提高效率
    时间复杂度: O(n) - 首次计算
    空间复杂度: O(n) - 缓存空间
    
    Args:
        n: 计算从1到n的和，默认为100
        
    Returns:
        int: 计算结果
    """
    if n <= 1:
        return n
    return n + method5_recursion_with_memoization(n - 1)


def method6_list_comprehension(n: int = 100) -> int:
    """
    方法6: 使用列表推导式和sum函数
    
    Python风格的简洁实现
    时间复杂度: O(n)
    空间复杂度: O(n) - 创建临时列表
    
    Args:
        n: 计算从1到n的和，默认为100
        
    Returns:
        int: 计算结果
    """
    return sum([i for i in range(1, n + 1)])


def method7_generator_expression(n: int = 100) -> int:
    """
    方法7: 使用生成器表达式
    
    内存效率更高的Python实现
    时间复杂度: O(n)
    空间复杂度: O(1) - 生成器不创建完整列表
    
    Args:
        n: 计算从1到n的和，默认为100
        
    Returns:
        int: 计算结果
    """
    return sum(i for i in range(1, n + 1))


def method8_reduce_function(n: int = 100) -> int:
    """
    方法8: 使用functools.reduce函数
    
    函数式编程风格的实现
    时间复杂度: O(n)
    空间复杂度: O(1)
    
    Args:
        n: 计算从1到n的和，默认为100
        
    Returns:
        int: 计算结果
    """
    from functools import reduce
    import operator
    return reduce(operator.add, range(1, n + 1), 0)


def benchmark_methods(n: int = 100) -> None:
    """
    性能基准测试函数
    
    比较不同方法的执行时间
    
    Args:
        n: 测试的数值范围
    """
    methods = [
        ("For循环", method1_for_loop),
        ("While循环", method2_while_loop),
        ("数学公式", method3_mathematical_formula),
        ("递归", method4_recursion),
        ("记忆化递归", method5_recursion_with_memoization),
        ("列表推导式", method6_list_comprehension),
        ("生成器表达式", method7_generator_expression),
        ("Reduce函数", method8_reduce_function),
    ]
    
    print(f"\n{'='*60}")
    print(f"性能基准测试 (n = {n})")
    print(f"{'='*60}")
    print(f"{'方法名称':<15} {'结果':<10} {'执行时间(秒)':<15} {'相对速度'}")
    print(f"{'-'*60}")
    
    results = []
    
    for name, method in methods:
        start_time = time.perf_counter()
        try:
            result = method(n)
            end_time = time.perf_counter()
            execution_time = end_time - start_time
            results.append((name, result, execution_time))
        except RecursionError:
            results.append((name, "递归深度超限", float('inf')))
    
    # 找到最快的方法作为基准
    min_time = min(r[2] for r in results if r[2] != float('inf'))
    
    for name, result, exec_time in results:
        if exec_time == float('inf'):
            print(f"{name:<15} {'N/A':<10} {'N/A':<15} {'N/A'}")
        else:
            relative_speed = exec_time / min_time if min_time > 0 else 1
            print(f"{name:<15} {result:<10} {exec_time:.8f}      {relative_speed:.2f}x")


def validate_all_methods(n: int = 100) -> bool:
    """
    验证所有方法的结果是否一致
    
    Args:
        n: 测试的数值范围
        
    Returns:
        bool: 所有方法结果是否一致
    """
    expected_result = method3_mathematical_formula(n)
    
    methods = [
        method1_for_loop,
        method2_while_loop,
        method4_recursion if n <= 1000 else None,  # 避免递归深度问题
        method5_recursion_with_memoization if n <= 1000 else None,
        method6_list_comprehension,
        method7_generator_expression,
        method8_reduce_function,
    ]
    
    print(f"\n{'='*50}")
    print(f"结果验证 (n = {n})")
    print(f"{'='*50}")
    print(f"期望结果: {expected_result}")
    print(f"{'-'*50}")
    
    all_correct = True
    
    for i, method in enumerate(methods, 1):
        if method is None:
            continue
            
        try:
            result = method(n)
            is_correct = result == expected_result
            status = "✅ 正确" if is_correct else "❌ 错误"
            print(f"方法{i}: {result} - {status}")
            
            if not is_correct:
                all_correct = False
                
        except Exception as e:
            print(f"方法{i}: 执行错误 - {str(e)}")
            all_correct = False
    
    return all_correct


def main():
    """主函数 - 演示所有实现方法"""
    print("🔢 计算1+2+3+...+100的和 - 多种实现方法")
    print("=" * 60)
    
    n = 100
    
    # 验证所有方法
    validation_result = validate_all_methods(n)
    print(f"\n所有方法验证结果: {'✅ 通过' if validation_result else '❌ 失败'}")
    
    # 性能基准测试
    benchmark_methods(n)
    
    # 展示推荐方法
    print(f"\n{'='*60}")
    print("💡 推荐方案分析")
    print(f"{'='*60}")
    
    recommendations = [
        ("🏆 最高效", "数学公式法", "O(1)时间复杂度，适合大数值计算"),
        ("🐍 最Python化", "生成器表达式", "简洁且内存效率高"),
        ("📚 最易理解", "For循环", "逻辑清晰，适合初学者"),
        ("🔄 函数式风格", "Reduce函数", "适合函数式编程爱好者"),
    ]
    
    for category, method, description in recommendations:
        print(f"{category}: {method} - {description}")
    
    print(f"\n🎯 最终答案: 1+2+3+...+100 = {method3_mathematical_formula(n)}")


if __name__ == "__main__":
    main()
