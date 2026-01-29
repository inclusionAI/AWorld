/**
 * 计算1+2+3+...+100的和 - JavaScript多种实现方法
 * 作者: DocCodeAgent
 * 日期: 2024年
 */

// 方法1: 使用for循环
function method1ForLoop(n = 100) {
    /**
     * 使用for循环计算求和
     * 时间复杂度: O(n), 空间复杂度: O(1)
     */
    let total = 0;
    for (let i = 1; i <= n; i++) {
        total += i;
    }
    return total;
}

// 方法2: 使用while循环
function method2WhileLoop(n = 100) {
    /**
     * 使用while循环计算求和
     * 时间复杂度: O(n), 空间复杂度: O(1)
     */
    let total = 0;
    let i = 1;
    while (i <= n) {
        total += i;
        i++;
    }
    return total;
}

// 方法3: 数学公式法
function method3MathFormula(n = 100) {
    /**
     * 使用高斯求和公式: n*(n+1)/2
     * 时间复杂度: O(1), 空间复杂度: O(1)
     */
    return Math.floor(n * (n + 1) / 2);
}

// 方法4: 递归实现
function method4Recursion(n = 100) {
    /**
     * 使用递归计算求和
     * 时间复杂度: O(n), 空间复杂度: O(n)
     */
    if (n <= 1) return n;
    return n + method4Recursion(n - 1);
}

// 方法5: 数组reduce方法
function method5ArrayReduce(n = 100) {
    /**
     * 使用数组的reduce方法
     * 时间复杂度: O(n), 空间复杂度: O(n)
     */
    return Array.from({length: n}, (_, i) => i + 1)
                .reduce((sum, num) => sum + num, 0);
}

// 方法6: 函数式编程风格
function method6Functional(n = 100) {
    /**
     * 使用函数式编程风格
     * 时间复杂度: O(n), 空间复杂度: O(n)
     */
    const range = (start, end) => 
        Array.from({length: end - start + 1}, (_, i) => start + i);
    
    return range(1, n).reduce((a, b) => a + b, 0);
}

// 方法7: ES6箭头函数简洁版
const method7ArrowFunction = (n = 100) => 
    Array.from({length: n}, (_, i) => i + 1)
         .reduce((sum, num) => sum + num, 0);

// 性能测试函数
function benchmarkMethods(n = 100) {
    const methods = [
        ['For循环', method1ForLoop],
        ['While循环', method2WhileLoop],
        ['数学公式', method3MathFormula],
        ['递归', method4Recursion],
        ['Array Reduce', method5ArrayReduce],
        ['函数式风格', method6Functional],
        ['箭头函数', method7ArrowFunction]
    ];

    console.log('\n' + '='.repeat(60));
    console.log(`JavaScript性能基准测试 (n = ${n})`);
    console.log('='.repeat(60));
    console.log('方法名称'.padEnd(15) + '结果'.padEnd(10) + '执行时间(ms)'.padEnd(15) + '相对速度');
    console.log('-'.repeat(60));

    const results = [];
    
    methods.forEach(([name, method]) => {
        const startTime = performance.now();
        try {
            const result = method(n);
            const endTime = performance.now();
            const executionTime = endTime - startTime;
            results.push([name, result, executionTime]);
        } catch (error) {
            results.push([name, 'Error', Infinity]);
        }
    });

    // 找到最快的方法
    const minTime = Math.min(...results.filter(r => r[2] !== Infinity).map(r => r[2]));

    results.forEach(([name, result, execTime]) => {
        if (execTime === Infinity) {
            console.log(name.padEnd(15) + 'N/A'.padEnd(10) + 'N/A'.padEnd(15) + 'N/A');
        } else {
            const relativeSpeed = execTime / minTime;
            console.log(
                name.padEnd(15) + 
                result.toString().padEnd(10) + 
                execTime.toFixed(6).padEnd(15) + 
                `${relativeSpeed.toFixed(2)}x`
            );
        }
    });
}

// 验证所有方法
function validateAllMethods(n = 100) {
    const expectedResult = method3MathFormula(n);
    const methods = [
        method1ForLoop,
        method2WhileLoop,
        method4Recursion,
        method5ArrayReduce,
        method6Functional,
        method7ArrowFunction
    ];

    console.log('\n' + '='.repeat(50));
    console.log(`JavaScript结果验证 (n = ${n})`);
    console.log('='.repeat(50));
    console.log(`期望结果: ${expectedResult}`);
    console.log('-'.repeat(50));

    let allCorrect = true;

    methods.forEach((method, i) => {
        try {
            const result = method(n);
            const isCorrect = result === expectedResult;
            const status = isCorrect ? '✅ 正确' : '❌ 错误';
            console.log(`方法${i + 1}: ${result} - ${status}`);
            
            if (!isCorrect) allCorrect = false;
        } catch (error) {
            console.log(`方法${i + 1}: 执行错误 - ${error.message}`);
            allCorrect = false;
        }
    });

    return allCorrect;
}

// 主函数
function main() {
    console.log('🔢 计算1+2+3+...+100的和 - JavaScript多种实现方法');
    console.log('='.repeat(60));
    
    const n = 100;
    
    // 验证所有方法
    const validationResult = validateAllMethods(n);
    console.log(`\n所有方法验证结果: ${validationResult ? '✅ 通过' : '❌ 失败'}`);
    
    // 性能基准测试
    benchmarkMethods(n);
    
    // 推荐方案
    console.log('\n' + '='.repeat(60));
    console.log('💡 JavaScript推荐方案分析');
    console.log('='.repeat(60));
    
    const recommendations = [
        ['🏆 最高效', '数学公式法', 'O(1)时间复杂度，性能最佳'],
        ['🎯 最现代', '箭头函数', 'ES6语法，简洁优雅'],
        ['📚 最易读', 'For循环', '逻辑清晰，适合初学者'],
        ['🔧 最实用', 'Array Reduce', '函数式编程，可读性好']
    ];
    
    recommendations.forEach(([category, method, description]) => {
        console.log(`${category}: ${method} - ${description}`);
    });
    
    console.log(`\n🎯 最终答案: 1+2+3+...+100 = ${method3MathFormula(n)}`);
}

// 执行主函数
main();
