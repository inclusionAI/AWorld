# coding: utf-8
"""
Base Coder Module - 分层式代码解析和生成系统

借鉴OpenCode的核心设计思想，提供代码解析、分析和生成的分层架构：
- Parser Layer: 代码解析层，负责代码结构解析
- Analysis Layer: 代码分析层，负责语义分析和理解
- Generation Layer: 代码生成层，负责智能代码生成
- Integration Layer: 集成层，与AWorld元学习架构集成

设计原则:
1. 单一职责原则 (SRP): 每个层级专注单一功能
2. 开放封闭原则 (OCP): 易于扩展新的解析器和生成器
3. 依赖注入 (DI): 支持不同实现的灵活替换
4. 最小化抽象: 避免过度设计，保持简洁
"""

import ast
import inspect
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Any, Union, Tuple, Set


# ==============================================================================
# 核心类型定义 (借鉴OpenCode的类型系统设计)
# ==============================================================================

class CodeElementType(Enum):
    """代码元素类型枚举"""
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    VARIABLE = "variable"
    IMPORT = "import"
    COMMENT = "comment"
    DOCSTRING = "docstring"
    MODULE = "module"


class AnalysisLevel(Enum):
    """分析深度级别"""
    SYNTAX = "syntax"      # 语法级别
    SEMANTIC = "semantic"   # 语义级别
    CONTEXT = "context"     # 上下文级别
    INTENT = "intent"       # 意图级别


@dataclass
class CodePosition:
    """代码位置信息 (类似OpenCode的位置跟踪)"""
    line: int
    column: int
    end_line: Optional[int] = None
    end_column: Optional[int] = None
    file_path: Optional[str] = None


@dataclass
class CodeElement:
    """代码元素基础数据结构"""
    name: str
    type: CodeElementType
    position: CodePosition
    source_code: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    children: List['CodeElement'] = field(default_factory=list)
    parent: Optional['CodeElement'] = None


@dataclass
class ParseResult:
    """解析结果封装 (借鉴OpenCode的结果封装模式)"""
    success: bool
    elements: List[CodeElement] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ==============================================================================
# Parser Layer - 代码解析层 (借鉴OpenCode的工具系统设计)
# ==============================================================================

class ICodeParser(ABC):
    """代码解析器接口 (遵循OpenCode的Tool接口模式)"""

    @property
    @abstractmethod
    def name(self) -> str:
        """解析器名称"""
        pass

    @property
    @abstractmethod
    def supported_extensions(self) -> Set[str]:
        """支持的文件扩展名"""
        pass

    @abstractmethod
    async def parse(self, source: str, file_path: Optional[str] = None) -> ParseResult:
        """
        解析源代码

        Args:
            source: 源代码字符串
            file_path: 文件路径（可选）

        Returns:
            ParseResult: 解析结果
        """
        pass

    @abstractmethod
    def validate_syntax(self, source: str) -> Tuple[bool, List[str]]:
        """
        验证语法正确性

        Args:
            source: 源代码字符串

        """
        pass


class PythonCodeParser(ICodeParser):
    """Python代码解析器 (借鉴OpenCode的具体工具实现模式)"""

    @property
    def name(self) -> str:
        return "python_parser"

    @property
    def supported_extensions(self) -> Set[str]:
        return {".py", ".pyx", ".pyi"}

    def validate_syntax(self, source: str) -> Tuple[bool, List[str]]:
        """验证Python代码语法"""
        try:
            ast.parse(source)
            return True, []
        except SyntaxError as e:
            error_msg = f"语法错误 (行 {e.lineno}): {e.msg}"
            return False, [error_msg]
        except Exception as e:
            return False, [f"解析错误: {str(e)}"]

    async def parse(self, source: str, file_path: Optional[str] = None) -> ParseResult:
        """解析Python源代码"""
        result = ParseResult(success=False)

        try:
            # 语法验证
            is_valid, errors = self.validate_syntax(source)
            if not is_valid:
                result.errors.extend(errors)
                return result

            # AST解析
            tree = ast.parse(source)
            elements = []

            # 遍历AST节点
            for node in ast.walk(tree):
                element = self._ast_node_to_element(node, source, file_path)
                if element:
                    elements.append(element)

            result.success = True
            result.elements = elements
            result.metadata = {
                "parser": self.name,
                "ast_node_count": len(list(ast.walk(tree))),
                "source_lines": len(source.splitlines())
            }

        except Exception as e:
            result.errors.append(f"解析失败: {str(e)}")

        return result

    def _ast_node_to_element(self, node: ast.AST, source: str, file_path: Optional[str]) -> Optional[CodeElement]:
        """将AST节点转换为CodeElement"""
        source_lines = source.splitlines()

        if isinstance(node, ast.FunctionDef):
            return self._create_function_element(node, source_lines, file_path)
        elif isinstance(node, ast.ClassDef):
            return self._create_class_element(node, source_lines, file_path)
        elif isinstance(node, ast.Assign):
            return self._create_variable_element(node, source_lines, file_path)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            return self._create_import_element(node, source_lines, file_path)

        return None

    def _create_function_element(self, node: ast.FunctionDef, source_lines: List[str], file_path: Optional[str]) -> CodeElement:
        """创建函数元素"""
        position = CodePosition(
            line=node.lineno,
            column=node.col_offset,
            end_line=getattr(node, 'end_lineno', None),
            end_column=getattr(node, 'end_col_offset', None),
            file_path=file_path
        )

        # 提取函数源代码
        source_code = self._extract_source_lines(source_lines, node.lineno, getattr(node, 'end_lineno', node.lineno))

        # 提取元数据
        metadata = {
            "args": [arg.arg for arg in node.args.args],
            "decorators": [ast.unparse(d) for d in node.decorator_list] if node.decorator_list else [],
            "docstring": ast.get_docstring(node),
            "is_async": isinstance(node, ast.AsyncFunctionDef)
        }

        return CodeElement(
            name=node.name,
            type=CodeElementType.FUNCTION,
            position=position,
            source_code=source_code,
            metadata=metadata
        )

    def _create_class_element(self, node: ast.ClassDef, source_lines: List[str], file_path: Optional[str]) -> CodeElement:
        """创建类元素"""
        position = CodePosition(
            line=node.lineno,
            column=node.col_offset,
            end_line=getattr(node, 'end_lineno', None),
            end_column=getattr(node, 'end_col_offset', None),
            file_path=file_path
        )

        source_code = self._extract_source_lines(source_lines, node.lineno, getattr(node, 'end_lineno', node.lineno))

        metadata = {
            "bases": [ast.unparse(base) for base in node.bases],
            "decorators": [ast.unparse(d) for d in node.decorator_list] if node.decorator_list else [],
            "docstring": ast.get_docstring(node),
            "methods": []
        }

        # 提取方法
        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                metadata["methods"].append(item.name)

        return CodeElement(
            name=node.name,
            type=CodeElementType.CLASS,
            position=position,
            source_code=source_code,
            metadata=metadata
        )

    def _create_variable_element(self, node: ast.Assign, source_lines: List[str], file_path: Optional[str]) -> Optional[CodeElement]:
        """创建变量元素"""
        if not node.targets or not isinstance(node.targets[0], ast.Name):
            return None

        var_name = node.targets[0].id
        position = CodePosition(
            line=node.lineno,
            column=node.col_offset,
            file_path=file_path
        )

        source_code = source_lines[node.lineno - 1] if node.lineno <= len(source_lines) else ""

        metadata = {
            "value": ast.unparse(node.value) if hasattr(ast, 'unparse') else str(node.value),
            "type_annotation": None
        }

        return CodeElement(
            name=var_name,
            type=CodeElementType.VARIABLE,
            position=position,
            source_code=source_code.strip(),
            metadata=metadata
        )

    def _create_import_element(self, node: Union[ast.Import, ast.ImportFrom], source_lines: List[str], file_path: Optional[str]) -> CodeElement:
        """创建导入元素"""
        position = CodePosition(
            line=node.lineno,
            column=node.col_offset,
            file_path=file_path
        )

        source_code = source_lines[node.lineno - 1] if node.lineno <= len(source_lines) else ""

        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
            metadata = {"type": "import", "names": names, "module": None}
            element_name = f"import {', '.join(names)}"
        else:  # ast.ImportFrom
            names = [alias.name for alias in node.names]
            metadata = {"type": "import_from", "names": names, "module": node.module}
            element_name = f"from {node.module} import {', '.join(names)}"

        return CodeElement(
            name=element_name,
            type=CodeElementType.IMPORT,
            position=position,
            source_code=source_code.strip(),
            metadata=metadata
        )

    def _extract_source_lines(self, source_lines: List[str], start_line: int, end_line: Optional[int]) -> str:
        """提取指定行范围的源代码"""
        if not end_line:
            end_line = start_line

        start_idx = max(0, start_line - 1)
        end_idx = min(len(source_lines), end_line)

        return '\n'.join(source_lines[start_idx:end_idx])


# ==============================================================================
# Analysis Layer - 代码分析层 (借鉴OpenCode的语义分析思想)
# ==============================================================================

@dataclass
class AnalysisResult:
    """分析结果封装"""
    success: bool
    insights: Dict[str, Any] = field(default_factory=dict)
    dependencies: List[str] = field(default_factory=list)
    complexity_metrics: Dict[str, float] = field(default_factory=dict)
    suggestions: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class ICodeAnalyzer(ABC):
    """代码分析器接口"""

    @property
    @abstractmethod
    def name(self) -> str:
        """分析器名称"""
        pass

    @abstractmethod
    async def analyze(self, elements: List[CodeElement], level: AnalysisLevel = AnalysisLevel.SEMANTIC) -> AnalysisResult:
        """
        分析代码元素

        Args:
            elements: 代码元素列表
            level: 分析深度级别

        Returns:
            AnalysisResult: 分析结果
        """
        pass


class SemanticAnalyzer(ICodeAnalyzer):
    """语义分析器 (借鉴OpenCode的分析模式)"""

    @property
    def name(self) -> str:
        return "semantic_analyzer"

    async def analyze(self, elements: List[CodeElement], level: AnalysisLevel = AnalysisLevel.SEMANTIC) -> AnalysisResult:
        """执行语义分析"""
        result = AnalysisResult(success=False)

        try:
            if level == AnalysisLevel.SYNTAX:
                result.insights = await self._syntax_analysis(elements)
            elif level == AnalysisLevel.SEMANTIC:
                result.insights = await self._semantic_analysis(elements)
            elif level == AnalysisLevel.CONTEXT:
                result.insights = await self._context_analysis(elements)
            elif level == AnalysisLevel.INTENT:
                result.insights = await self._intent_analysis(elements)

            result.dependencies = self._extract_dependencies(elements)
            result.complexity_metrics = self._calculate_complexity(elements)
            result.suggestions = self._generate_suggestions(elements)
            result.success = True

        except Exception as e:
            result.errors.append(f"分析失败: {str(e)}")

        return result

    async def _syntax_analysis(self, elements: List[CodeElement]) -> Dict[str, Any]:
        """语法级别分析"""
        return {
            "total_elements": len(elements),
            "element_types": {elem_type.value: len([e for e in elements if e.type == elem_type])
                            for elem_type in CodeElementType},
            "average_line_length": sum(len(e.source_code.splitlines()) for e in elements) / max(len(elements), 1)
        }

    async def _semantic_analysis(self, elements: List[CodeElement]) -> Dict[str, Any]:
        """语义级别分析"""
        functions = [e for e in elements if e.type == CodeElementType.FUNCTION]
        classes = [e for e in elements if e.type == CodeElementType.CLASS]

        return {
            "functions": {
                "count": len(functions),
                "avg_params": sum(len(f.metadata.get("args", [])) for f in functions) / max(len(functions), 1),
                "has_docstring": len([f for f in functions if f.metadata.get("docstring")]) / max(len(functions), 1)
            },
            "classes": {
                "count": len(classes),
                "avg_methods": sum(len(c.metadata.get("methods", [])) for c in classes) / max(len(classes), 1),
                "inheritance_depth": self._calculate_inheritance_depth(classes)
            },
            "code_patterns": self._identify_patterns(elements)
        }

    async def _context_analysis(self, elements: List[CodeElement]) -> Dict[str, Any]:
        """上下文级别分析"""
        return {
            "module_structure": self._analyze_module_structure(elements),
            "coupling_metrics": self._calculate_coupling(elements),
            "cohesion_metrics": self._calculate_cohesion(elements)
        }

    async def _intent_analysis(self, elements: List[CodeElement]) -> Dict[str, Any]:
        """意图级别分析"""
        return {
            "design_patterns": self._detect_design_patterns(elements),
            "code_smells": self._detect_code_smells(elements),
            "refactoring_opportunities": self._identify_refactoring_opportunities(elements)
        }

    def _extract_dependencies(self, elements: List[CodeElement]) -> List[str]:
        """提取依赖关系"""
        dependencies = set()
        for element in elements:
            if element.type == CodeElementType.IMPORT:
                if element.metadata.get("module"):
                    dependencies.add(element.metadata["module"])
                dependencies.update(element.metadata.get("names", []))
        return list(dependencies)

    def _calculate_complexity(self, elements: List[CodeElement]) -> Dict[str, float]:
        """计算复杂度指标"""
        functions = [e for e in elements if e.type == CodeElementType.FUNCTION]

        return {
            "cyclomatic_complexity": sum(self._cyclomatic_complexity(f) for f in functions) / max(len(functions), 1),
            "cognitive_complexity": sum(self._cognitive_complexity(f) for f in functions) / max(len(functions), 1),
            "lines_of_code": sum(len(e.source_code.splitlines()) for e in elements),
            "maintainability_index": self._calculate_maintainability_index(elements)
        }

    def _generate_suggestions(self, elements: List[CodeElement]) -> List[str]:
        """生成改进建议"""
        suggestions = []
        functions = [e for e in elements if e.type == CodeElementType.FUNCTION]

        # 检查函数长度
        for func in functions:
            line_count = len(func.source_code.splitlines())
            if line_count > 50:
                suggestions.append(f"函数 {func.name} 过长 ({line_count} 行)，建议拆分")

        # 检查文档字符串
        undocumented = [f for f in functions if not f.metadata.get("docstring")]
        if undocumented:
            suggestions.append(f"发现 {len(undocumented)} 个函数缺少文档字符串")

        return suggestions

    # 简化的复杂度计算方法
    def _cyclomatic_complexity(self, element: CodeElement) -> float:
        """简化的圈复杂度计算"""
        complexity_keywords = ['if', 'elif', 'for', 'while', 'try', 'except', 'and', 'or']
        return sum(1 for keyword in complexity_keywords if keyword in element.source_code) + 1

    def _cognitive_complexity(self, element: CodeElement) -> float:
        """简化的认知复杂度计算"""
        return self._cyclomatic_complexity(element) * 1.2

    def _calculate_maintainability_index(self, elements: List[CodeElement]) -> float:
        """简化的维护性指数"""
        total_lines = sum(len(e.source_code.splitlines()) for e in elements)
        return max(0, 171 - 5.2 * (total_lines / 100) - 0.23 * 10 - 16.2 * (total_lines / 1000))

    # 简化的其他分析方法
    def _calculate_inheritance_depth(self, classes: List[CodeElement]) -> float:
        """计算平均继承深度"""
        return sum(len(c.metadata.get("bases", [])) for c in classes) / max(len(classes), 1)

    def _identify_patterns(self, elements: List[CodeElement]) -> Dict[str, int]:
        """识别代码模式"""
        return {"singleton": 0, "factory": 0, "observer": 0}

    def _analyze_module_structure(self, elements: List[CodeElement]) -> Dict[str, Any]:
        """分析模块结构"""
        return {"imports": len([e for e in elements if e.type == CodeElementType.IMPORT])}

    def _calculate_coupling(self, elements: List[CodeElement]) -> float:
        """计算耦合度"""
        return 0.5

    def _calculate_cohesion(self, elements: List[CodeElement]) -> float:
        """计算内聚度"""
        return 0.8

    def _detect_design_patterns(self, elements: List[CodeElement]) -> List[str]:
        """检测设计模式"""
        return []

    def _detect_code_smells(self, elements: List[CodeElement]) -> List[str]:
        """检测代码异味"""
        return []

    def _identify_refactoring_opportunities(self, elements: List[CodeElement]) -> List[str]:
        """识别重构机会"""
        return []


# ==============================================================================
# Generation Layer - 代码生成层 (借鉴OpenCode的编辑工具思想)
# ==============================================================================

@dataclass
class GenerationRequest:
    """代码生成请求"""
    intent: str                          # 生成意图
    context: Dict[str, Any]              # 上下文信息
    existing_code: Optional[str] = None  # 现有代码
    style_guide: Dict[str, Any] = field(default_factory=dict)  # 代码风格指南
    constraints: List[str] = field(default_factory=list)       # 约束条件


@dataclass
class GenerationResult:
    """代码生成结果"""
    success: bool
    generated_code: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    explanations: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class ICodeGenerator(ABC):
    """代码生成器接口 (类似OpenCode的工具接口模式)"""

    @property
    @abstractmethod
    def name(self) -> str:
        """生成器名称"""
        pass

    @abstractmethod
    async def generate(self, request: GenerationRequest) -> GenerationResult:
        """
        生成代码

        Args:
            request: 生成请求

        Returns:
            GenerationResult: 生成结果
        """
        pass

    @abstractmethod
    def validate_request(self, request: GenerationRequest) -> Tuple[bool, List[str]]:
        """
        验证生成请求

        Args:
            request: 生成请求

        Returns:
            Tuple[bool, List[str]]: (是否有效, 错误信息)
        """
        pass


class TemplateCodeGenerator(ICodeGenerator):
    """基于模板的代码生成器 (借鉴OpenCode的模式)"""

    @property
    def name(self) -> str:
        return "template_generator"

    def validate_request(self, request: GenerationRequest) -> Tuple[bool, List[str]]:
        """验证生成请求"""
        errors = []
        if not request.intent:
            errors.append("生成意图不能为空")
        return len(errors) == 0, errors

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        """基于模板生成代码"""
        result = GenerationResult(success=False)

        try:
            # 验证请求
            is_valid, errors = self.validate_request(request)
            if not is_valid:
                result.errors.extend(errors)
                return result

            # 如果有现有代码，进行智能修改
            if request.existing_code:
                generated_code = await self._modify_existing_code(request)
            else:
                # 从头生成新代码
                template = self._select_template(request.intent)
                if not template:
                    result.errors.append(f"未找到适合的模板: {request.intent}")
                    return result
                generated_code = self._render_template(template, request)

            result.generated_code = generated_code
            result.success = True
            result.explanations.append(f"基于意图 '{request.intent}' 生成代码")

        except Exception as e:
            result.errors.append(f"代码生成失败: {str(e)}")

        return result

    async def _modify_existing_code(self, request: GenerationRequest) -> str:
        """智能修改现有代码"""
        existing_code = request.existing_code
        intent = request.intent.lower()
        context = request.context

        # 获取分析结果
        analysis = context.get("analysis", {})
        suggestions = context.get("suggestions", [])

        modifications = []

        # 根据不同的修改意图执行不同的策略
        if "docstring" in intent or "documentation" in intent:
            modified_code = self._add_docstrings(existing_code)
            modifications.append("添加文档字符串")

        elif "type hint" in intent or "typing" in intent:
            modified_code = self._add_type_hints(existing_code)
            modifications.append("添加类型提示")

        elif "error handling" in intent or "exception" in intent:
            modified_code = self._add_error_handling(existing_code)
            modifications.append("添加错误处理")

        elif "logging" in intent:
            modified_code = self._add_logging(existing_code)
            modifications.append("添加日志记录")

        elif "optimize" in intent or "performance" in intent:
            modified_code = self._optimize_code(existing_code, analysis)
            modifications.append("性能优化")

        else:
            # 默认：应用分析建议
            modified_code = self._apply_suggestions(existing_code, suggestions)
            modifications.append("应用分析建议")

        return modified_code

    def _add_docstrings(self, code: str) -> str:
        """为函数和类添加文档字符串"""
        lines = code.split('\n')
        result_lines = []
        i = 0

        while i < len(lines):
            line = lines[i].strip()

            # 检查是否是函数或类定义
            if line.startswith('def ') or line.startswith('class '):
                result_lines.append(lines[i])
                i += 1

                # 检查下一行是否已有文档字符串
                if i < len(lines) and '"""' not in lines[i]:
                    # 获取缩进
                    indent = len(lines[i-1]) - len(lines[i-1].lstrip())
                    indent_str = ' ' * (indent + 4)

                    # 添加文档字符串
                    if line.startswith('def '):
                        func_name = line.split('(')[0].replace('def ', '')
                        docstring = f'{indent_str}"""{func_name}函数的功能描述"""\n'
                    else:  # class
                        class_name = line.split('(')[0].split(':')[0].replace('class ', '')
                        docstring = f'{indent_str}"""{class_name}类的功能描述"""\n'

                    result_lines.append(docstring)
            else:
                result_lines.append(lines[i])
                i += 1

        return '\n'.join(result_lines)

    def _add_type_hints(self, code: str) -> str:
        """添加基础类型提示"""
        import re

        # 简单的类型推断和添加
        # 为函数参数添加基本类型提示
        def add_hints(match):
            func_line = match.group(0)
            if '->' not in func_line and 'self' not in func_line:
                # 简单启发式：参数名暗示类型
                if 'name' in func_line or 'text' in func_line or 'msg' in func_line:
                    func_line = func_line.replace('):', ') -> str:')
                elif 'count' in func_line or 'num' in func_line or 'size' in func_line:
                    func_line = func_line.replace('):', ') -> int:')
                else:
                    func_line = func_line.replace('):', ') -> Any:')
            return func_line

        # 为没有类型提示的函数添加返回类型
        pattern = r'def\s+\w+\([^)]*\):'
        modified_code = re.sub(pattern, add_hints, code)

        # 如果添加了Any，确保导入
        if '-> Any:' in modified_code and 'from typing import' not in modified_code:
            modified_code = 'from typing import Any\n\n' + modified_code

        return modified_code

    def _add_error_handling(self, code: str) -> str:
        """添加基础错误处理"""
        lines = code.split('\n')
        result_lines = []
        i = 0

        while i < len(lines):
            line = lines[i]

            # 为文件操作添加try-except
            if 'open(' in line or 'read(' in line or 'write(' in line:
                # 获取缩进
                indent = len(line) - len(line.lstrip())
                indent_str = ' ' * indent

                result_lines.append(f'{indent_str}try:')
                result_lines.append(' ' * 4 + line)

                # 查找代码块的结束
                j = i + 1
                while j < len(lines) and (lines[j].startswith(' ' * (indent + 4)) or lines[j].strip() == ''):
                    result_lines.append(lines[j])
                    j += 1

                result_lines.append(f'{indent_str}except Exception as e:')
                result_lines.append(f'{indent_str}    print(f"操作失败: {{e}}")')
                result_lines.append(f'{indent_str}    raise')

                i = j - 1
            else:
                result_lines.append(line)

            i += 1

        return '\n'.join(result_lines)

    def _add_logging(self, code: str) -> str:
        """添加日志记录"""
        lines = code.split('\n')

        # 添加logging导入
        if 'import logging' not in code:
            lines.insert(0, 'import logging')
            lines.insert(1, '')

        # 为函数入口添加日志
        result_lines = []
        for line in lines:
            if line.strip().startswith('def ') and '__init__' not in line:
                result_lines.append(line)
                # 获取缩进并添加日志
                indent = len(line) - len(line.lstrip())
                func_name = line.strip().split('(')[0].replace('def ', '')
                result_lines.append(' ' * (indent + 4) + f'logging.info(f"调用函数: {func_name}")')
            else:
                result_lines.append(line)

        return '\n'.join(result_lines)

    def _optimize_code(self, code: str, analysis: Dict[str, Any]) -> str:
        """基础代码优化"""
        # 简单的优化：移除多余的空行，优化导入语句等
        lines = code.split('\n')
        optimized_lines = []

        prev_line_empty = False
        for line in lines:
            # 移除连续的空行
            if line.strip() == '':
                if not prev_line_empty:
                    optimized_lines.append(line)
                prev_line_empty = True
            else:
                optimized_lines.append(line)
                prev_line_empty = False

        return '\n'.join(optimized_lines)

    def _apply_suggestions(self, code: str, suggestions: List[str]) -> str:
        """应用分析建议"""
        modified_code = code

        for suggestion in suggestions:
            if "缺少文档字符串" in suggestion:
                modified_code = self._add_docstrings(modified_code)
            elif "过长" in suggestion:
                # 简单处理：添加TODO注释
                modified_code += '\n# TODO: 考虑重构长函数以提高可读性'

        return modified_code

    def _select_template(self, intent: str) -> Optional[Dict[str, Any]]:
        """根据意图选择模板"""
        templates = {
            "function": {
                "name": "function_template",
                "template": """def {name}({params}):
    \"\"\"{docstring}\"\"\"
    {body}
    return {return_value}"""
            },
            "class": {
                "name": "class_template",
                "template": """class {name}({bases}):
    \"\"\"{docstring}\"\"\"

    def __init__(self, {init_params}):
        {init_body}

    {methods}"""
            },
            "test": {
                "name": "test_template",
                "template": """def test_{name}():
    \"\"\"{docstring}\"\"\"
    # Arrange
    {arrange}

    # Act
    {act}

    # Assert
    {assert_}"""
            }
        }

        for key in templates:
            if key in intent.lower():
                return templates[key]
        return None

    def _render_template(self, template: Dict[str, Any], request: GenerationRequest) -> str:
        """渲染模板"""
        template_str = template["template"]
        context = request.context

        # 简单的模板替换
        for key, value in context.items():
            placeholder = "{" + key + "}"
            if placeholder in template_str:
                template_str = template_str.replace(placeholder, str(value))

        # 处理未替换的占位符
        import re
        placeholders = re.findall(r'\{(\w+)\}', template_str)
        for placeholder in placeholders:
            template_str = template_str.replace(f"{{{placeholder}}}", f"# TODO: 实现 {placeholder}")

        return template_str


# ==============================================================================
# Integration Layer - 集成层 (与AWorld MetaLearning架构集成)
# ==============================================================================

class BaseCoderIntegration:
    """
    BaseCoder与AWorld元学习架构的集成层
    借鉴OpenCode的Registry模式实现组件注册和管理
    """

    def __init__(self):
        self.parsers: Dict[str, ICodeParser] = {}
        self.analyzers: Dict[str, ICodeAnalyzer] = {}
        self.generators: Dict[str, ICodeGenerator] = {}

        # 注册默认组件
        self._register_default_components()

    def _register_default_components(self):
        """注册默认组件"""
        # 注册解析器
        python_parser = PythonCodeParser()
        self.register_parser(python_parser)

        # 注册分析器
        semantic_analyzer = SemanticAnalyzer()
        self.register_analyzer(semantic_analyzer)

        # 注册生成器
        template_generator = TemplateCodeGenerator()
        self.register_generator(template_generator)

    def register_parser(self, parser: ICodeParser):
        """注册解析器"""
        self.parsers[parser.name] = parser

    def register_analyzer(self, analyzer: ICodeAnalyzer):
        """注册分析器"""
        self.analyzers[analyzer.name] = analyzer

    def register_generator(self, generator: ICodeGenerator):
        """注册生成器"""
        self.generators[generator.name] = generator

    async def parse_and_analyze(self, source_code: str, file_path: Optional[str] = None,
                              parser_name: str = "python_parser",
                              analyzer_name: str = "semantic_analyzer",
                              analysis_level: AnalysisLevel = AnalysisLevel.SEMANTIC) -> Tuple[ParseResult, AnalysisResult]:
        """
        解析和分析代码（集成操作）

        Args:
            source_code: 源代码
            file_path: 文件路径
            parser_name: 解析器名称
            analyzer_name: 分析器名称
            analysis_level: 分析级别

        Returns:
            Tuple[ParseResult, AnalysisResult]: 解析结果和分析结果
        """
        # 解析
        parser = self.parsers.get(parser_name)
        if not parser:
            raise ValueError(f"未找到解析器: {parser_name}")

        parse_result = await parser.parse(source_code, file_path)

        # 分析
        analyzer = self.analyzers.get(analyzer_name)
        if not analyzer:
            raise ValueError(f"未找到分析器: {analyzer_name}")

        analysis_result = await analyzer.analyze(parse_result.elements, analysis_level)

        return parse_result, analysis_result

    async def generate_code(self, intent: str, context: Dict[str, Any],
                           generator_name: str = "template_generator",
                           existing_code: Optional[str] = None) -> GenerationResult:
        """
        生成代码

        Args:
            intent: 生成意图
            context: 上下文信息
            generator_name: 生成器名称
            existing_code: 现有代码

        Returns:
            GenerationResult: 生成结果
        """
        generator = self.generators.get(generator_name)
        if not generator:
            raise ValueError(f"未找到生成器: {generator_name}")

        request = GenerationRequest(
            intent=intent,
            context=context,
            existing_code=existing_code
        )

        return await generator.generate(request)

    def get_supported_languages(self) -> Dict[str, Set[str]]:
        """获取支持的编程语言"""
        languages = {}
        for name, parser in self.parsers.items():
            languages[name] = parser.supported_extensions
        return languages

    async def full_workflow(self, source_code: str, intent: str, context: Dict[str, Any],
                           file_path: Optional[str] = None) -> Dict[str, Any]:
        """
        完整的工作流：解析 -> 分析 -> 生成

        Args:
            source_code: 源代码
            intent: 生成意图
            context: 上下文信息
            file_path: 文件路径

        Returns:
            Dict[str, Any]: 完整的工作流结果
        """
        try:
            # 1. 解析和分析
            parse_result, analysis_result = await self.parse_and_analyze(source_code, file_path)

            # 2. 将分析结果添加到上下文
            enhanced_context = {**context}
            if analysis_result.success:
                enhanced_context["analysis"] = analysis_result.insights
                enhanced_context["dependencies"] = analysis_result.dependencies
                enhanced_context["complexity"] = analysis_result.complexity_metrics

            # 3. 生成代码
            generation_result = await self.generate_code(intent, enhanced_context, existing_code=source_code)

            return {
                "success": True,
                "parse": {
                    "success": parse_result.success,
                    "elements_count": len(parse_result.elements),
                    "errors": parse_result.errors
                },
                "analysis": {
                    "success": analysis_result.success,
                    "insights": analysis_result.insights,
                    "suggestions": analysis_result.suggestions,
                    "errors": analysis_result.errors
                },
                "generation": {
                    "success": generation_result.success,
                    "generated_code": generation_result.generated_code,
                    "explanations": generation_result.explanations,
                    "errors": generation_result.errors
                }
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }


# ==============================================================================
# 全局实例 - 便于其他模块使用
# ==============================================================================

# 创建全局BaseCoder集成实例
base_coder = BaseCoderIntegration()


# ==============================================================================
# 扩展功能 - 目录处理和Patch生成 (借鉴OpenCode的文件操作思想)
# ==============================================================================

import os
import shutil
import difflib
from pathlib import Path

@dataclass
class CodePatch:
    """代码补丁数据结构"""
    file_path: str
    original_content: str
    modified_content: str
    patch_content: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class DirectoryCodeProcessor:
    """目录代码处理器"""

    def __init__(self, base_coder_instance: BaseCoderIntegration):
        self.base_coder = base_coder_instance

    async def parse_directory(self, directory_path: str, extensions: Set[str] = {".py"}) -> Dict[str, Any]:
        """
        解析目录下的所有代码文件

        Args:
            directory_path: 目录路径
            extensions: 支持的文件扩展名

        Returns:
            Dict[str, Any]: 解析结果汇总
        """
        directory_path = Path(directory_path)
        results = {
            "success": True,
            "files": {},
            "summary": {
                "total_files": 0,
                "parsed_files": 0,
                "total_elements": 0,
                "errors": []
            }
        }

        try:
            # 遍历目录中的所有文件
            for file_path in directory_path.rglob("*"):
                if file_path.is_file() and file_path.suffix in extensions:
                    results["summary"]["total_files"] += 1

                    try:
                        # 读取文件内容
                        content = file_path.read_text(encoding='utf-8')

                        # 解析文件
                        parse_result, analysis_result = await self.base_coder.parse_and_analyze(
                            content, str(file_path)
                        )

                        if parse_result.success:
                            results["summary"]["parsed_files"] += 1
                            results["summary"]["total_elements"] += len(parse_result.elements)

                            results["files"][str(file_path.relative_to(directory_path))] = {
                                "parse": parse_result,
                                "analysis": analysis_result,
                                "content": content
                            }
                        else:
                            results["summary"]["errors"].extend(parse_result.errors)

                    except Exception as e:
                        error_msg = f"处理文件 {file_path} 时出错: {str(e)}"
                        results["summary"]["errors"].append(error_msg)

        except Exception as e:
            results["success"] = False
            results["summary"]["errors"].append(f"目录处理失败: {str(e)}")

        return results

    async def generate_code_patches(self, parse_results: Dict[str, Any],
                                   modification_intent: str) -> List[CodePatch]:
        """
        为解析的代码生成修改补丁

        Args:
            parse_results: 目录解析结果
            modification_intent: 修改意图

        Returns:
            List[CodePatch]: 生成的补丁列表
        """
        patches = []

        for relative_path, file_data in parse_results["files"].items():
            try:
                original_content = file_data["content"]
                analysis_result = file_data["analysis"]

                # 基于分析结果构建生成上下文
                context = {
                    "analysis": analysis_result.insights if analysis_result.success else {},
                    "suggestions": analysis_result.suggestions if analysis_result.success else [],
                    "file_path": relative_path,
                    "intent": modification_intent
                }

                # 生成修改后的代码
                generation_result = await self.base_coder.generate_code(
                    intent=modification_intent,
                    context=context,
                    existing_code=original_content
                )

                if generation_result.success and generation_result.generated_code:
                    modified_content = generation_result.generated_code

                    # 生成patch
                    patch_content = self._create_unified_diff(
                        original_content, modified_content, relative_path
                    )

                    patch = CodePatch(
                        file_path=relative_path,
                        original_content=original_content,
                        modified_content=modified_content,
                        patch_content=patch_content,
                        metadata={
                            "generation_explanations": generation_result.explanations,
                            "modification_intent": modification_intent
                        }
                    )
                    patches.append(patch)

            except Exception as e:
                print(f"为文件 {relative_path} 生成补丁时出错: {str(e)}")

        return patches

    def _create_unified_diff(self, original: str, modified: str, filename: str) -> str:
        """创建统一格式的diff"""
        original_lines = original.splitlines(keepends=True)
        modified_lines = modified.splitlines(keepends=True)

        diff = difflib.unified_diff(
            original_lines,
            modified_lines,
            fromfile=f"a/{filename}",
            tofile=f"b/{filename}",
            lineterm=''
        )

        return ''.join(diff)

    def copy_directory(self, source_dir: str, target_dir: str) -> bool:
        """
        复制目录

        Args:
            source_dir: 源目录
            target_dir: 目标目录

        Returns:
            bool: 是否成功
        """
        try:
            source_path = Path(source_dir)
            target_path = Path(target_dir)

            # 如果目标目录存在，先删除
            if target_path.exists():
                shutil.rmtree(target_path)

            # 复制整个目录树
            shutil.copytree(source_path, target_path)
            return True

        except Exception as e:
            print(f"复制目录失败: {str(e)}")
            return False

    def apply_patches(self, patches: List[CodePatch], target_directory: str) -> Dict[str, Any]:
        """
        将补丁应用到目标目录

        Args:
            patches: 补丁列表
            target_directory: 目标目录

        Returns:
            Dict[str, Any]: 应用结果
        """
        target_path = Path(target_directory)
        results = {
            "success": True,
            "applied_patches": 0,
            "failed_patches": 0,
            "errors": []
        }

        for patch in patches:
            try:
                file_path = target_path / patch.file_path

                # 确保目录存在
                file_path.parent.mkdir(parents=True, exist_ok=True)

                # 写入修改后的内容
                file_path.write_text(patch.modified_content, encoding='utf-8')
                results["applied_patches"] += 1

                print(f"已应用补丁: {patch.file_path}")

            except Exception as e:
                error_msg = f"应用补丁 {patch.file_path} 失败: {str(e)}"
                results["errors"].append(error_msg)
                results["failed_patches"] += 1

        if results["failed_patches"] > 0:
            results["success"] = False

        return results