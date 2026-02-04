"""
AWorld AST Framework - Python解析器
==================================

基于tree-sitter-python库的Python代码解析器实现。
直接复用官方tree_sitter_python库，减少自定义代码量。
"""

from typing import List, Optional, Dict, Any
from pathlib import Path

try:
    import tree_sitter_python as tspython
    from tree_sitter import Language, Parser, Query, QueryCursor
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False

from .base_parser import BaseParser
from ..models import Symbol, Reference, CodeNode, SymbolType, ReferenceType


class PythonParser(BaseParser):
    """Python Tree-sitter解析器，直接使用tree_sitter_python库"""

    def __init__(self):
        super().__init__(
            language="python",
            file_extensions={'.py', '.pyi', '.pyx'}
        )

        # 初始化tree-sitter组件
        if not TREE_SITTER_AVAILABLE:
            self.logger.error("tree_sitter_python库未安装，请运行: pip install tree-sitter tree-sitter-python")
            self._language = None
            self._parser = None
            self._queries = {}
            return

        try:
            # 创建Language对象
            self._language = Language(tspython.language())

            # 创建Parser对象
            self._parser = Parser(self._language)

            # 编译查询
            self._queries = self._compile_queries()

            self.logger.info("Python Tree-sitter解析器初始化成功")

        except Exception as e:
            self.logger.error(f"初始化Python解析器失败: {e}")
            self._language = None
            self._parser = None
            self._queries = {}

    def _compile_queries(self) -> Dict[str, "Query"]:
        """编译所有常用的查询模式"""
        if not self._language:
            return {}

        queries = {}

        try:
            # 函数定义查询（只匹配顶层函数，不包括类方法）
            # 使用 module 作为父节点限定，确保只捕获模块级别的函数
            queries['functions'] = Query(self._language, """
                (function_definition
                    name: (identifier) @function_name
                    parameters: (parameters) @function_params
                    body: (block) @function_body) @function_def
            """)

            # 类定义查询
            queries['classes'] = Query(self._language, """
                (class_definition
                  name: (identifier) @class_name
                  superclasses: (argument_list)? @class_bases
                  body: (block) @class_body) @class_def
            """)

            # 方法定义查询（在类内部的函数） - 修正版本
            queries['methods'] = Query(self._language, """
                (class_definition
                  body: (block
                    (function_definition
                      name: (identifier) @method_name
                      parameters: (parameters) @method_params
                      body: (block) @method_body) @method_def))
            """)

            # 异步方法查询 - 修正版本（使用正确的节点类型）
            queries['async_methods'] = Query(self._language, """
                (class_definition
                  body: (block
                    (function_definition
                      name: (identifier) @async_method_name
                      parameters: (parameters) @async_method_params
                      body: (block) @async_method_body) @async_method_def))
            """)

            # 变量赋值查询
            queries['variables'] = Query(self._language, """
                (assignment
                  left: (identifier) @variable_name) @variable_assign

                (assignment
                  left: (pattern_list (identifier) @multi_variable_name)) @multi_variable_assign
            """)

            # 导入查询
            queries['imports'] = Query(self._language, """
                (import_statement
                  name: (dotted_name) @import_name) @import_stmt

                (import_from_statement
                  module_name: (dotted_name)? @from_module
                  name: (dotted_name) @from_name) @from_import_stmt

                (import_from_statement
                  module_name: (dotted_name)? @from_module_list
                  name: (aliased_import
                    (dotted_name) @from_name_list)) @from_import_alias_stmt
            """)

            # 函数调用查询
            queries['calls'] = Query(self._language, """
                (call
                  function: [
                    (identifier) @call_name
                    (attribute
                      attribute: (identifier) @method_call_name)
                  ]) @call_expr
            """)

            self.logger.debug("Python查询编译成功")
            return queries

        except Exception as e:
            self.logger.error(f"编译Python查询失败: {e}")
            return {}

    def parse_file(self, file_path: Path) -> CodeNode:
        """
        解析Python文件，返回CodeNode
        直接使用tree_sitter_python库，无需依赖base_parser的实现
        """
        if not file_path.exists():
            self.logger.warning(f"文件不存在: {file_path}")
            return CodeNode(file_path=file_path)

        if not self._parser or not self._language:
            self.logger.error("Python解析器未正确初始化")
            return CodeNode(file_path=file_path)

        try:
            # 读取文件内容
            content = file_path.read_text(encoding='utf-8')
            source_bytes = content.encode('utf-8')

            # 使用tree-sitter解析
            tree = self._parser.parse(source_bytes)

            if tree.root_node.has_error:
                self.logger.warning(f"解析时发现语法错误: {file_path}")

            # 提取符号和引用
            symbols = self._extract_symbols(tree, content, file_path)
            references = self._extract_references(tree, content, file_path)

            # 提取导入和导出
            imports = self._extract_imports_native(tree, content)
            exports = self._extract_exports_native(tree, content)

            return CodeNode(
                file_path=file_path,
                symbols=symbols,
                references=references,
                imports=imports,
                exports=exports,
                last_modified=file_path.stat().st_mtime,
                metadata={'language': self.language, 'parser': 'tree_sitter_python'}
            )

        except Exception as e:
            self.logger.error(f"解析文件失败 {file_path}: {e}")
            return CodeNode(file_path=file_path)

    def _extract_symbols(self, tree, content: str, file_path: Path) -> List[Symbol]:
        """提取符号定义"""
        symbols = []
        lines = content.split('\n')

        # 提取类
        classes = self._extract_classes(tree, content, file_path, lines)
        symbols.extend(classes)

        # 提取函数
        functions = self._extract_functions(tree, content, file_path, lines)

        # 提取方法
        methods = self._extract_methods(tree, content, file_path, lines)

        # 过滤重名的函数：如果函数名与方法名重复，则过滤掉该函数
        method_names = {method.name for method in methods}
        filtered_functions = [func for func in functions if func.name not in method_names]

        # 如果有过滤，记录日志
        filtered_count = len(functions) - len(filtered_functions)
        if filtered_count > 0:
            filtered_names = [func.name for func in functions if func.name in method_names]
            self.logger.debug(f"过滤了 {filtered_count} 个与方法重名的函数: {filtered_names}")

        symbols.extend(filtered_functions)
        symbols.extend(methods)

        # 提取变量
        symbols.extend(self._extract_variables(tree, content, file_path, lines))

        return symbols

    def _extract_classes(self, tree, content: str, file_path: Path, lines: List[str]) -> List[Symbol]:
        """提取类定义"""
        classes = []

        if 'classes' not in self._queries:
            return classes

        try:
            cursor = QueryCursor(self._queries['classes'])
            captures = cursor.captures(tree.root_node)

            class_names = captures.get('class_name', [])
            class_bodies = captures.get('class_body', [])
            class_defs = captures.get('class_def', [])

            # 遍历类定义
            for i, name_node in enumerate(class_names):
                class_name = name_node.text.decode('utf-8')

                # 提取文档字符串
                docstring = None
                if i < len(class_bodies):
                    docstring = self._extract_docstring_from_body(class_bodies[i], content)

                # 获取对应的完整类定义节点
                class_def_node = class_defs[i] if i < len(class_defs) else name_node

                # 提取类的完整代码内容
                class_content = self._extract_code_content(class_def_node, content)

                # 创建类符号
                symbol = Symbol(
                    name=class_name,
                    symbol_type=SymbolType.CLASS,
                    file_path=file_path,
                    line_number=name_node.start_point[0] + 1,
                    column=name_node.start_point[1],
                    end_line=class_def_node.end_point[0] + 1,
                    end_column=class_def_node.end_point[1],
                    signature=self._extract_class_signature(class_def_node, lines),
                    docstring=docstring,
                    content=class_content,
                    metadata={'node_type': 'class_definition', 'language': 'python'}
                )
                classes.append(symbol)

        except Exception as e:
            self.logger.error(f"提取类定义失败: {e}")

        return classes

    def _extract_functions(self, tree, content: str, file_path: Path, lines: List[str]) -> List[Symbol]:
        """提取顶层函数定义（不包括类方法）"""
        functions = []

        # 提取模块级别的函数（查询已限定为 module 下的函数）
        if 'functions' in self._queries:
            try:
                cursor = QueryCursor(self._queries['functions'])
                captures = cursor.captures(tree.root_node)

                func_names = captures.get('function_name', [])
                func_params = captures.get('function_params', [])
                func_bodies = captures.get('function_body', [])
                func_defs = captures.get('function_def', [])

                for i, name_node in enumerate(func_names):
                    function_name = name_node.text.decode('utf-8')

                    # 检查是否是异步函数
                    is_async = False
                    func_def_node = func_defs[i] if i < len(func_defs) else name_node
                    if func_def_node.children and func_def_node.children[0].type == 'async':
                        is_async = True

                    # 提取参数
                    parameters = []
                    if i < len(func_params):
                        parameters = self._extract_parameters(func_params[i])

                    # 提取文档字符串
                    docstring = None
                    if i < len(func_bodies):
                        docstring = self._extract_docstring_from_body(func_bodies[i], content)

                    # 构建modifiers
                    modifiers = set()
                    if is_async:
                        modifiers.add('async')

                    # 提取函数的完整代码内容
                    func_content = self._extract_code_content(func_def_node, content)

                    symbol = Symbol(
                        name=function_name,
                        symbol_type=SymbolType.FUNCTION,
                        file_path=file_path,
                        line_number=name_node.start_point[0] + 1,
                        column=name_node.start_point[1],
                        end_line=func_def_node.end_point[0] + 1,
                        end_column=func_def_node.end_point[1],
                        signature=self._extract_function_signature(func_def_node, lines),
                        docstring=docstring,
                        content=func_content,
                        parameters=parameters,
                        modifiers=modifiers,
                        metadata={
                            'node_type': 'async_function_definition' if is_async else 'function_definition',
                            'language': 'python'
                        }
                    )
                    functions.append(symbol)

            except Exception as e:
                self.logger.error(f"提取函数失败: {e}")

        return functions

    def _extract_methods(self, tree, content: str, file_path: Path, lines: List[str]) -> List[Symbol]:
        """提取方法定义"""
        methods = []

        if 'methods' not in self._queries:
            return methods

        try:
            cursor = QueryCursor(self._queries['methods'])
            matches = cursor.matches(tree.root_node)

            for match in matches:
                pattern_index, captures_dict = match

                # 找到父类名
                parent_class_name = self._find_parent_class_name(
                    captures_dict.get('method_def', [None])[0] or
                    captures_dict.get('async_method_def', [None])[0]
                )

                # 普通方法
                if 'method_name' in captures_dict:
                    name_node = captures_dict['method_name'][0]
                    method_name = name_node.text.decode('utf-8')

                    parameters = []
                    if 'method_params' in captures_dict:
                        parameters = self._extract_parameters(captures_dict['method_params'][0])

                    docstring = None
                    if 'method_body' in captures_dict:
                        docstring = self._extract_docstring_from_body(captures_dict['method_body'][0], content)

                    # 提取方法的完整代码内容
                    method_content = self._extract_code_content(captures_dict['method_def'][0], content)

                    symbol = Symbol(
                        name=method_name,
                        symbol_type=SymbolType.METHOD,
                        file_path=file_path,
                        line_number=name_node.start_point[0] + 1,
                        column=name_node.start_point[1],
                        end_line=captures_dict['method_def'][0].end_point[0] + 1,
                        end_column=captures_dict['method_def'][0].end_point[1],
                        signature=self._extract_function_signature(captures_dict['method_def'][0], lines),
                        docstring=docstring,
                        content=method_content,
                        parent=parent_class_name,
                        parameters=parameters,
                        metadata={'node_type': 'method_definition', 'language': 'python'}
                    )
                    methods.append(symbol)

                # 异步方法
                elif 'async_method_name' in captures_dict:
                    name_node = captures_dict['async_method_name'][0]
                    method_def_node = captures_dict['async_method_def'][0]

                    # 检查是否真的是异步方法（第一个子节点是否为 async）
                    is_async = (method_def_node.children and
                               method_def_node.children[0].type == 'async')

                    # 只有真正的异步方法才处理
                    if is_async:
                        method_name = name_node.text.decode('utf-8')

                        parameters = []
                        if 'async_method_params' in captures_dict:
                            parameters = self._extract_parameters(captures_dict['async_method_params'][0])

                        docstring = None
                        if 'async_method_body' in captures_dict:
                            docstring = self._extract_docstring_from_body(captures_dict['async_method_body'][0], content)

                        # 提取异步方法的完整代码内容
                        async_method_content = self._extract_code_content(method_def_node, content)

                        symbol = Symbol(
                            name=method_name,
                            symbol_type=SymbolType.METHOD,
                            file_path=file_path,
                            line_number=name_node.start_point[0] + 1,
                            column=name_node.start_point[1],
                            end_line=method_def_node.end_point[0] + 1,
                            end_column=method_def_node.end_point[1],
                            signature=self._extract_function_signature(method_def_node, lines),
                            docstring=docstring,
                            content=async_method_content,
                            parent=parent_class_name,
                            parameters=parameters,
                            modifiers={'async'},
                            metadata={'node_type': 'async_method_definition', 'language': 'python'}
                        )
                        methods.append(symbol)

        except Exception as e:
            self.logger.error(f"提取方法定义失败: {e}")

        return methods

    def _extract_variables(self, tree, content: str, file_path: Path, lines: List[str]) -> List[Symbol]:
        """提取变量定义"""
        variables = []

        if 'variables' not in self._queries:
            return variables

        try:
            cursor = QueryCursor(self._queries['variables'])
            matches = cursor.matches(tree.root_node)

            for match in matches:
                pattern_index, captures_dict = match

                # 单变量赋值
                if 'variable_name' in captures_dict:
                    name_node = captures_dict['variable_name'][0]
                    variable_name = name_node.text.decode('utf-8')

                    # 判断是否是常量（全大写）
                    symbol_type = SymbolType.CONSTANT if variable_name.isupper() else SymbolType.VARIABLE

                    # 获取完整的赋值语句（从赋值节点获取）
                    assignment_node = captures_dict.get('variable_assign', [None])[0]
                    variable_content = self._extract_code_content(assignment_node, content) if assignment_node else None

                    symbol = Symbol(
                        name=variable_name,
                        symbol_type=symbol_type,
                        file_path=file_path,
                        line_number=name_node.start_point[0] + 1,
                        column=name_node.start_point[1],
                        end_line=name_node.end_point[0] + 1,
                        end_column=name_node.end_point[1],
                        content=variable_content,
                        metadata={'node_type': 'variable_assignment', 'language': 'python'}
                    )
                    variables.append(symbol)

                # 多变量赋值
                elif 'multi_variable_name' in captures_dict:
                    name_node = captures_dict['multi_variable_name'][0]
                    variable_name = name_node.text.decode('utf-8')

                    symbol_type = SymbolType.CONSTANT if variable_name.isupper() else SymbolType.VARIABLE

                    # 获取完整的多变量赋值语句
                    multi_assignment_node = captures_dict.get('multi_variable_assign', [None])[0]
                    multi_variable_content = self._extract_code_content(multi_assignment_node, content) if multi_assignment_node else None

                    symbol = Symbol(
                        name=variable_name,
                        symbol_type=symbol_type,
                        file_path=file_path,
                        line_number=name_node.start_point[0] + 1,
                        column=name_node.start_point[1],
                        end_line=name_node.end_point[0] + 1,
                        end_column=name_node.end_point[1],
                        content=multi_variable_content,
                        metadata={'node_type': 'multi_variable_assignment', 'language': 'python'}
                    )
                    variables.append(symbol)

        except Exception as e:
            self.logger.error(f"提取变量定义失败: {e}")

        return variables

    def _extract_references(self, tree, content: str, file_path: Path) -> List[Reference]:
        """提取引用"""
        references = []

        if 'calls' not in self._queries:
            return references

        try:
            cursor = QueryCursor(self._queries['calls'])
            matches = cursor.matches(tree.root_node)

            for match in matches:
                pattern_index, captures_dict = match

                # 函数调用
                if 'call_name' in captures_dict:
                    name_node = captures_dict['call_name'][0]
                    call_name = name_node.text.decode('utf-8')

                    reference = Reference(
                        symbol_name=call_name,
                        reference_type=ReferenceType.CALL,
                        file_path=file_path,
                        line_number=name_node.start_point[0] + 1,
                        column=name_node.start_point[1],
                        metadata={'node_type': 'function_call', 'language': 'python'}
                    )
                    references.append(reference)

                # 方法调用
                elif 'method_call_name' in captures_dict:
                    name_node = captures_dict['method_call_name'][0]
                    method_name = name_node.text.decode('utf-8')

                    reference = Reference(
                        symbol_name=method_name,
                        reference_type=ReferenceType.CALL,
                        file_path=file_path,
                        line_number=name_node.start_point[0] + 1,
                        column=name_node.start_point[1],
                        metadata={'node_type': 'method_call', 'language': 'python'}
                    )
                    references.append(reference)

        except Exception as e:
            self.logger.error(f"提取引用失败: {e}")

        return references

    def _extract_imports_native(self, tree, content: str) -> List[str]:
        """使用tree-sitter提取导入语句"""
        imports = []

        if 'imports' not in self._queries:
            return imports

        try:
            cursor = QueryCursor(self._queries['imports'])
            captures = cursor.captures(tree.root_node)

            # import语句
            import_names = captures.get('import_name', [])
            for node in import_names:
                module_name = node.text.decode('utf-8').split('.')[0]
                if module_name:
                    imports.append(module_name)

            # from import语句
            from_modules = captures.get('from_module', [])
            for node in from_modules:
                module_name = node.text.decode('utf-8').split('.')[0]
                if module_name and not module_name.startswith('.'):
                    imports.append(module_name)

            # from import列表语句
            from_module_lists = captures.get('from_module_list', [])
            for node in from_module_lists:
                module_name = node.text.decode('utf-8').split('.')[0]
                if module_name and not module_name.startswith('.'):
                    imports.append(module_name)

            # from import别名语句
            from_name_lists = captures.get('from_name_list', [])
            for node in from_name_lists:
                module_name = node.text.decode('utf-8').split('.')[0]
                if module_name:
                    imports.append(module_name)

        except Exception as e:
            self.logger.error(f"提取导入失败: {e}")

        return list(set(imports))  # 去重

    def _extract_exports_native(self, tree, content: str) -> List[str]:
        """使用tree-sitter提取导出语句（__all__）"""
        exports = []

        try:
            # 查找__all__赋值
            all_query = Query(self._language, """
                (assignment
                  left: (identifier) @all_var
                  right: (list) @all_list)
                (#eq? @all_var "__all__")
            """)

            cursor = QueryCursor(all_query)
            captures = cursor.captures(tree.root_node)

            all_lists = captures.get('all_list', [])
            for list_node in all_lists:
                # 遍历列表项
                for child in list_node.children:
                    if child.type == 'string':
                        export_name = child.text.decode('utf-8').strip('\'"')
                        exports.append(export_name)

        except Exception as e:
            self.logger.error(f"提取导出失败: {e}")

        return exports

    # ==============================
    # 辅助方法
    # ==============================

    def _extract_code_content(self, node, content: str) -> Optional[str]:
        """提取节点对应的完整代码内容"""
        try:
            if not node:
                return None

            # 获取节点在源码中的位置
            start_line = node.start_point[0]
            end_line = node.end_point[0]
            start_col = node.start_point[1]
            end_col = node.end_point[1]

            # 分割文件内容为行
            lines = content.split('\n')

            # 提取对应的代码行
            if start_line == end_line:
                # 单行代码
                if start_line < len(lines):
                    line = lines[start_line]
                    return line[start_col:end_col]
            else:
                # 多行代码
                result_lines = []

                # 第一行（从start_col开始）
                if start_line < len(lines):
                    result_lines.append(lines[start_line][start_col:])

                # 中间行（完整行）
                for line_idx in range(start_line + 1, end_line):
                    if line_idx < len(lines):
                        result_lines.append(lines[line_idx])

                # 最后一行（到end_col结束）
                if end_line < len(lines):
                    result_lines.append(lines[end_line][:end_col])

                return '\n'.join(result_lines)

        except Exception as e:
            self.logger.debug(f"提取代码内容失败: {e}")

        return None

    def _extract_docstring_from_body(self, body_node, content: str) -> Optional[str]:
        """从函数/类体中提取文档字符串"""
        try:
            # 查找第一个表达式语句
            for child in body_node.children:
                if child.type == 'expression_statement':
                    for expr in child.children:
                        if expr.type == 'string':
                            docstring = expr.text.decode('utf-8')
                            # 清理文档字符串格式
                            docstring = docstring.strip('"""\'\'\'')
                            return docstring.strip()
        except Exception as e:
            self.logger.debug(f"提取文档字符串失败: {e}")
        return None

    def _extract_parameters(self, params_node) -> List[str]:
        """提取函数参数"""
        parameters = []
        try:
            for child in params_node.children:
                if child.type == 'identifier':
                    param_name = child.text.decode('utf-8')
                    parameters.append(param_name)
                elif child.type == 'default_parameter':
                    # 默认参数，取第一个子节点（参数名）
                    for param_child in child.children:
                        if param_child.type == 'identifier':
                            param_name = param_child.text.decode('utf-8')
                            parameters.append(param_name)
                            break
                elif child.type == 'typed_parameter':
                    # 类型注解参数，取第一个子节点（参数名）
                    for param_child in child.children:
                        if param_child.type == 'identifier':
                            param_name = param_child.text.decode('utf-8')
                            parameters.append(param_name)
                            break
        except Exception as e:
            self.logger.debug(f"提取参数失败: {e}")
        return parameters

    def _find_parent_class_name(self, method_node) -> Optional[str]:
        """查找方法的父类名"""
        try:
            if not method_node:
                return None

            # 向上遍历找到类定义
            current = method_node.parent
            while current:
                if current.type == 'class_definition':
                    for child in current.children:
                        if child.type == 'identifier':
                            return child.text.decode('utf-8')
                current = current.parent
        except Exception as e:
            self.logger.debug(f"查找父类名失败: {e}")
        return None

    def _extract_class_signature(self, class_node, lines: List[str]) -> Optional[str]:
        """提取类签名"""
        try:
            start_line = class_node.start_point[0]
            end_line = min(class_node.end_point[0], start_line + 2)  # 最多看3行

            signature_lines = []
            for i in range(start_line, end_line + 1):
                if i < len(lines):
                    line = lines[i].strip()
                    signature_lines.append(line)
                    if line.endswith(':'):
                        break

            return ' '.join(signature_lines) if signature_lines else None
        except Exception as e:
            self.logger.debug(f"提取类签名失败: {e}")
            return None

    def _extract_function_signature(self, function_node, lines: List[str]) -> Optional[str]:
        """提取函数签名"""
        try:
            start_line = function_node.start_point[0]
            end_line = function_node.end_point[0]

            # 查找函数定义的结束位置（冒号）
            for i in range(start_line, min(end_line + 1, start_line + 5)):
                if i < len(lines):
                    line = lines[i].strip()
                    if line.endswith(':'):
                        # 构建签名
                        signature_lines = []
                        for j in range(start_line, i + 1):
                            if j < len(lines):
                                signature_lines.append(lines[j].strip())
                        return ' '.join(signature_lines)

            # 如果没找到冒号，返回第一行
            return lines[start_line].strip() if start_line < len(lines) else None
        except Exception as e:
            self.logger.debug(f"提取函数签名失败: {e}")
            return None

    # ==============================
    # 保持兼容性的方法（为了与BaseParser接口一致）
    # ==============================

    def _get_default_query(self) -> str:
        """为了与BaseParser兼容，返回空查询"""
        return ""

    def generate_skeleton(self, content: str, file_path: Path) -> str:
        """
        生成Python代码骨架
        优化版本：直接使用tree-sitter提取关键结构
        """
        if not self._parser or not self._language:
            return f"# 解析器未初始化\n# 文件: {file_path}\n"

        try:
            source_bytes = content.encode('utf-8')
            tree = self._parser.parse(source_bytes)

            if tree.root_node.has_error:
                self.logger.warning(f"生成骨架时发现语法错误: {file_path}")

            # 提取符号
            symbols = self._extract_symbols(tree, content, file_path)

            # 生成骨架
            lines = content.split('\n')
            skeleton_lines = []

            # 添加文件头
            skeleton_lines.append(f"# 文件: {file_path}")
            skeleton_lines.append("")

            # 按行号排序符号
            symbols_by_line = {}
            for symbol in symbols:
                line = symbol.line_number
                if line not in symbols_by_line:
                    symbols_by_line[line] = []
                symbols_by_line[line].append(symbol)

            # 生成骨架内容
            for line_num in sorted(symbols_by_line.keys()):
                for symbol in symbols_by_line[line_num]:
                    if symbol.signature:
                        skeleton_lines.append(f"{line_num:4d}: {symbol.signature}")
                        if symbol.docstring:
                            doc_preview = symbol.docstring.split('\n')[0][:60]
                            skeleton_lines.append(f"      # {doc_preview}")
                    else:
                        skeleton_lines.append(f"{line_num:4d}: {symbol.symbol_type.value}: {symbol.name}")

            return '\n'.join(skeleton_lines)

        except Exception as e:
            self.logger.error(f"生成骨架失败 {file_path}: {e}")
            return f"# 骨架生成失败: {e}\n# 文件: {file_path}\n"