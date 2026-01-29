/**
 * 计算1+2+3+...+100的和 - Java多种实现方法
 * 作者: DocCodeAgent
 * 日期: 2024年
 */

import java.util.Arrays;
import java.util.function.IntUnaryOperator;
import java.util.stream.IntStream;

public class Sum1To100 {
    
    /**
     * 方法1: 使用for循环
     * 时间复杂度: O(n), 空间复杂度: O(1)
     */
    public static int method1ForLoop(int n) {
        int total = 0;
        for (int i = 1; i <= n; i++) {
            total += i;
        }
        return total;
    }
    
    /**
     * 方法2: 使用while循环
     * 时间复杂度: O(n), 空间复杂度: O(1)
     */
    public static int method2WhileLoop(int n) {
        int total = 0;
        int i = 1;
        while (i <= n) {
            total += i;
            i++;
        }
        return total;
    }
    
    /**
     * 方法3: 数学公式法 - 高斯求和公式
     * 时间复杂度: O(1), 空间复杂度: O(1)
     */
    public static int method3MathFormula(int n) {
        return n * (n + 1) / 2;
    }
    
    /**
     * 方法4: 递归实现
     * 时间复杂度: O(n), 空间复杂度: O(n)
     */
    public static int method4Recursion(int n) {
        if (n <= 1) return n;
        return n + method4Recursion(n - 1);
    }
    
    /**
     * 方法5: Java 8 Stream API
     * 时间复杂度: O(n), 空间复杂度: O(1)
     */
    public static int method5StreamAPI(int n) {
        return IntStream.rangeClosed(1, n).sum();
    }
    
    /**
     * 方法6: Java 8 Stream Reduce
     * 时间复杂度: O(n), 空间复杂度: O(1)
     */
    public static int method6StreamReduce(int n) {
        return IntStream.rangeClosed(1, n)
                       .reduce(0, Integer::sum);
    }
    
    /**
     * 方法7: 并行Stream (适合大数据量)
     * 时间复杂度: O(n), 空间复杂度: O(1)
     */
    public static int method7ParallelStream(int n) {
        return IntStream.rangeClosed(1, n)
                       .parallel()
                       .sum();
    }
    
    // 辅助类
    static class TestMethod {
        String name;
        IntUnaryOperator method;
        
        TestMethod(String name, IntUnaryOperator method) {
            this.name = name;
            this.method = method;
        }
    }
    
    static class TestResult {
        String name;
        int result;
        long executionTime;
        
        TestResult(String name, int result, long executionTime) {
            this.name = name;
            this.result = result;
            this.executionTime = executionTime;
        }
    }
    
    /**
     * 性能基准测试
     */
    public static void benchmarkMethods(int n) {
        System.out.println("\n" + "=".repeat(60));
        System.out.println("Java性能基准测试 (n = " + n + ")");
        System.out.println("=".repeat(60));
        System.out.printf("%-15s %-10s %-15s %s%n", "方法名称", "结果", "执行时间(ns)", "相对速度");
        System.out.println("-".repeat(60));
        
        // 定义测试方法
        TestMethod[] methods = {
            new TestMethod("For循环", Sum1To100::method1ForLoop),
            new TestMethod("While循环", Sum1To100::method2WhileLoop),
            new TestMethod("数学公式", Sum1To100::method3MathFormula),
            new TestMethod("递归", Sum1To100::method4Recursion),
            new TestMethod("Stream API", Sum1To100::method5StreamAPI),
            new TestMethod("Stream Reduce", Sum1To100::method6StreamReduce),
            new TestMethod("并行Stream", Sum1To100::method7ParallelStream)
        };
        
        TestResult[] results = new TestResult[methods.length];
        
        // 执行测试
        for (int i = 0; i < methods.length; i++) {
            long startTime = System.nanoTime();
            try {
                int result = methods[i].method.applyAsInt(n);
                long endTime = System.nanoTime();
                long executionTime = endTime - startTime;
                results[i] = new TestResult(methods[i].name, result, executionTime);
            } catch (Exception e) {
                results[i] = new TestResult(methods[i].name, -1, Long.MAX_VALUE);
            }
        }
        
        // 找到最快的方法
        long minTime = Arrays.stream(results)
                            .filter(r -> r.executionTime != Long.MAX_VALUE)
                            .mapToLong(r -> r.executionTime)
                            .min()
                            .orElse(1);
        
        // 输出结果
        for (TestResult result : results) {
            if (result.executionTime == Long.MAX_VALUE) {
                System.out.printf("%-15s %-10s %-15s %s%n", 
                    result.name, "N/A", "N/A", "N/A");
            } else {
                double relativeSpeed = (double) result.executionTime / minTime;
                System.out.printf("%-15s %-10d %-15d %.2fx%n", 
                    result.name, result.result, result.executionTime, relativeSpeed);
            }
        }
    }
    
    /**
     * 验证所有方法的正确性
     */
    public static boolean validateAllMethods(int n) {
        int expectedResult = method3MathFormula(n);
        
        System.out.println("\n" + "=".repeat(50));
        System.out.println("Java结果验证 (n = " + n + ")");
        System.out.println("=".repeat(50));
        System.out.println("期望结果: " + expectedResult);
        System.out.println("-".repeat(50));
        
        TestMethod[] methods = {
            new TestMethod("方法1", Sum1To100::method1ForLoop),
            new TestMethod("方法2", Sum1To100::method2WhileLoop),
            new TestMethod("方法3", Sum1To100::method4Recursion),
            new TestMethod("方法4", Sum1To100::method5StreamAPI),
            new TestMethod("方法5", Sum1To100::method6StreamReduce),
            new TestMethod("方法6", Sum1To100::method7ParallelStream)
        };
        
        boolean allCorrect = true;
        
        for (int i = 0; i < methods.length; i++) {
            try {
                int result = methods[i].method.applyAsInt(n);
                boolean isCorrect = result == expectedResult;
                String status = isCorrect ? "✅ 正确" : "❌ 错误";
                System.out.println("方法" + (i + 1) + ": " + result + " - " + status);
                
                if (!isCorrect) allCorrect = false;
            } catch (Exception e) {
                System.out.println("方法" + (i + 1) + ": 执行错误 - " + e.getMessage());
                allCorrect = false;
            }
        }
        
        return allCorrect;
    }
    
    /**
     * 主函数
     */
    public static void main(String[] args) {
        System.out.println("🔢 计算1+2+3+...+100的和 - Java多种实现方法");
        System.out.println("=".repeat(60));
        
        int n = 100;
        
        // 验证所有方法
        boolean validationResult = validateAllMethods(n);
        System.out.println("\n所有方法验证结果: " + (validationResult ? "✅ 通过" : "❌ 失败"));
        
        // 性能基准测试
        benchmarkMethods(n);
        
        // 推荐方案分析
        System.out.println("\n" + "=".repeat(60));
        System.out.println("💡 Java推荐方案分析");
        System.out.println("=".repeat(60));
        
        String[][] recommendations = {
            {"🏆 最高效", "数学公式法", "O(1)时间复杂度，性能最佳"},
            {"🚀 最现代", "Stream API", "Java 8+语法，简洁易读"},
            {"📚 最经典", "For循环", "传统Java风格，易于理解"},
            {"⚡ 最并发", "并行Stream", "适合大数据量的并行处理"}
        };
        
        for (String[] rec : recommendations) {
            System.out.println(rec[0] + ": " + rec[1] + " - " + rec[2]);
        }
        
        System.out.println("\n🎯 最终答案: 1+2+3+...+100 = " + method3MathFormula(n));
    }
}
