"""
AWorld AST Framework - Python Parser
=====================================

Python code parser implementation based on tree-sitter-python library.
Directly reuses the official tree_sitter_python library to reduce custom code.
"""

from pathlib import Path
from typing import List, Optional, Dict

from ..utils import logger

try:
    import tree_sitter_python as tspython
    from tree_sitter import Language, Parser, Query, QueryCursor
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False

from .base_parser import BaseParser
from ..models import Symbol, Reference, CodeNode, SymbolType, ReferenceType


class PythonParser(BaseParser):
    """Python Tree-sitter parser, directly uses tree_sitter_python library"""

    def __init__(self):
        super().__init__(
            language="python",
            file_extensions={'.py', '.pyi', '.pyx'}
        )

        # Initialize tree-sitter components
        if not TREE_SITTER_AVAILABLE:
            logger.error("tree_sitter_python library not installed, please run: pip install tree-sitter tree-sitter-python")
            self._language = None
            self._parser = None
            self._queries = {}
            return

        try:
            # Create Language object
            self._language = Language(tspython.language())

            # Create Parser object
            self._parser = Parser(self._language)

            # Compile queries
            self._queries = self._compile_queries()

            logger.info("Python Tree-sitter parser initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize Python parser: {e}")
            self._language = None
            self._parser = None
            self._queries = {}

    def _compile_queries(self) -> Dict[str, "Query"]:
        """Compile all commonly used query patterns"""
        if not self._language:
            return {}

        queries = {}

        try:
            # Function definition query (only matches top-level functions, excludes class methods)
            # Use module as parent node constraint to ensure only module-level functions are captured
            queries['functions'] = Query(self._language, """
                (function_definition
                    name: (identifier) @function_name
                    parameters: (parameters) @function_params
                    body: (block) @function_body) @function_def
            """)

            # Class definition query
            queries['classes'] = Query(self._language, """
                (class_definition
                  name: (identifier) @class_name
                  superclasses: (argument_list)? @class_bases
                  body: (block) @class_body) @class_def
            """)

            # Method definition query (functions inside classes) - corrected version
            queries['methods'] = Query(self._language, """
                (class_definition
                  body: (block
                    (function_definition
                      name: (identifier) @method_name
                      parameters: (parameters) @method_params
                      body: (block) @method_body) @method_def))
            """)

            # Async method query - corrected version (using correct node type)
            queries['async_methods'] = Query(self._language, """
                (class_definition
                  body: (block
                    (function_definition
                      name: (identifier) @async_method_name
                      parameters: (parameters) @async_method_params
                      body: (block) @async_method_body) @async_method_def))
            """)

            # Variable assignment query
            queries['variables'] = Query(self._language, """
                (assignment
                  left: (identifier) @variable_name) @variable_assign

                (assignment
                  left: (pattern_list (identifier) @multi_variable_name)) @multi_variable_assign
            """)

            # Import query
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

            # Function call query
            queries['calls'] = Query(self._language, """
                (call
                  function: [
                    (identifier) @call_name
                    (attribute
                      attribute: (identifier) @method_call_name)
                  ]) @call_expr
            """)

            logger.debug("Python queries compiled successfully")
            return queries

        except Exception as e:
            logger.error(f"Failed to compile Python queries: {e}")
            return {}

    def parse_file(self, file_path: Path) -> CodeNode:
        """
        Parse Python file and return CodeNode
        Directly uses tree_sitter_python library, no need to depend on base_parser implementation
        """
        if not file_path.exists():
            logger.warning(f"File does not exist: {file_path}")
            return CodeNode(file_path=file_path)

        if not self._parser or not self._language:
            logger.error("Python parser not properly initialized")
            return CodeNode(file_path=file_path)

        try:
            # Read file content
            content = file_path.read_text(encoding='utf-8')
            source_bytes = content.encode('utf-8')

            # Parse with tree-sitter
            tree = self._parser.parse(source_bytes)

            if tree.root_node.has_error:
                logger.warning(f"Syntax errors found during parsing: {file_path}")

            # Extract symbols and references
            symbols = self._extract_symbols(tree, content, file_path)
            references = self._extract_references(tree, content, file_path)

            # Extract imports and exports
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
            logger.error(f"Failed to parse file {file_path}: {e}")
            return CodeNode(file_path=file_path)

    def _extract_symbols(self, tree, content: str, file_path: Path) -> List[Symbol]:
        """Extract symbol definitions"""
        symbols = []
        lines = content.split('\n')

        # Extract classes
        classes = self._extract_classes(tree, content, file_path, lines)
        symbols.extend(classes)

        # Extract functions
        functions = self._extract_functions(tree, content, file_path, lines)

        # Extract methods
        methods = self._extract_methods(tree, content, file_path, lines)

        # Filter functions with duplicate names: if function name duplicates method name, filter out the function
        method_names = {method.name for method in methods}
        filtered_functions = [func for func in functions if func.name not in method_names]

        # Log if filtering occurred
        filtered_count = len(functions) - len(filtered_functions)
        if filtered_count > 0:
            filtered_names = [func.name for func in functions if func.name in method_names]
            logger.debug(f"Filtered {filtered_count} functions with duplicate method names: {filtered_names}")

        symbols.extend(filtered_functions)
        symbols.extend(methods)

        # Extract variables
        symbols.extend(self._extract_variables(tree, content, file_path, lines))

        return symbols

    def _extract_classes(self, tree, content: str, file_path: Path, lines: List[str]) -> List[Symbol]:
        """Extract class definitions"""
        classes = []

        if 'classes' not in self._queries:
            return classes

        try:
            cursor = QueryCursor(self._queries['classes'])
            captures = cursor.captures(tree.root_node)

            class_names = captures.get('class_name', [])
            class_bodies = captures.get('class_body', [])
            class_defs = captures.get('class_def', [])

            # Iterate through class definitions
            for i, name_node in enumerate(class_names):
                class_name = name_node.text.decode('utf-8')

                # Extract docstring
                docstring = None
                if i < len(class_bodies):
                    docstring = self._extract_docstring_from_body(class_bodies[i], content)

                # Get corresponding complete class definition node
                class_def_node = class_defs[i] if i < len(class_defs) else name_node

                # Extract complete code content of the class
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
            logger.error(f"Failed to extract class definitions: {e}")

        return classes

    def _extract_functions(self, tree, content: str, file_path: Path, lines: List[str]) -> List[Symbol]:
        """Extract top-level function definitions (excluding class methods)"""
        functions = []

        # Extract module-level functions (query is already limited to functions under module)
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

                    # Check if it's an async function
                    is_async = False
                    func_def_node = func_defs[i] if i < len(func_defs) else name_node
                    if func_def_node.children and func_def_node.children[0].type == 'async':
                        is_async = True

                    # Extract parameters
                    parameters = []
                    if i < len(func_params):
                        parameters = self._extract_parameters(func_params[i])

                    # Extract docstring
                    docstring = None
                    if i < len(func_bodies):
                        docstring = self._extract_docstring_from_body(func_bodies[i], content)

                    # Build modifiers
                    modifiers = set()
                    if is_async:
                        modifiers.add('async')

                    # Extract complete code content of the function
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
                logger.error(f"Failed to extract functions: {e}")

        return functions

    def _extract_methods(self, tree, content: str, file_path: Path, lines: List[str]) -> List[Symbol]:
        """Extract method definitions"""
        methods = []

        if 'methods' not in self._queries:
            return methods

        try:
            cursor = QueryCursor(self._queries['methods'])
            matches = cursor.matches(tree.root_node)

            for match in matches:
                pattern_index, captures_dict = match

                # Find parent class name
                parent_class_name = self._find_parent_class_name(
                    captures_dict.get('method_def', [None])[0] or
                    captures_dict.get('async_method_def', [None])[0]
                )

                # Regular methods
                if 'method_name' in captures_dict:
                    name_node = captures_dict['method_name'][0]
                    method_name = name_node.text.decode('utf-8')

                    parameters = []
                    if 'method_params' in captures_dict:
                        parameters = self._extract_parameters(captures_dict['method_params'][0])

                    docstring = None
                    if 'method_body' in captures_dict:
                        docstring = self._extract_docstring_from_body(captures_dict['method_body'][0], content)

                    # Extract complete code content of the method
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

                # Async methods
                elif 'async_method_name' in captures_dict:
                    name_node = captures_dict['async_method_name'][0]
                    method_def_node = captures_dict['async_method_def'][0]

                    # Check if it's really an async method (whether first child node is async)
                    is_async = (method_def_node.children and
                               method_def_node.children[0].type == 'async')

                    # Only process true async methods
                    if is_async:
                        method_name = name_node.text.decode('utf-8')

                        parameters = []
                        if 'async_method_params' in captures_dict:
                            parameters = self._extract_parameters(captures_dict['async_method_params'][0])

                        docstring = None
                        if 'async_method_body' in captures_dict:
                            docstring = self._extract_docstring_from_body(captures_dict['async_method_body'][0], content)

                        # Extract complete code content of the async method
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
            logger.error(f"Failed to extract method definitions: {e}")

        return methods

    def _extract_variables(self, tree, content: str, file_path: Path, lines: List[str]) -> List[Symbol]:
        """Extract variable definitions"""
        variables = []

        if 'variables' not in self._queries:
            return variables

        try:
            cursor = QueryCursor(self._queries['variables'])
            matches = cursor.matches(tree.root_node)

            for match in matches:
                pattern_index, captures_dict = match

                # Single variable assignment
                if 'variable_name' in captures_dict:
                    name_node = captures_dict['variable_name'][0]
                    variable_name = name_node.text.decode('utf-8')

                    # Determine if it's a constant (all uppercase)
                    symbol_type = SymbolType.CONSTANT if variable_name.isupper() else SymbolType.VARIABLE

                    # Get complete assignment statement (from assignment node)
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

                # Multiple variable assignment
                elif 'multi_variable_name' in captures_dict:
                    name_node = captures_dict['multi_variable_name'][0]
                    variable_name = name_node.text.decode('utf-8')

                    symbol_type = SymbolType.CONSTANT if variable_name.isupper() else SymbolType.VARIABLE

                    # Get complete multiple variable assignment statement
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
            logger.error(f"Failed to extract variable definitions: {e}")

        return variables

    def _extract_references(self, tree, content: str, file_path: Path) -> List[Reference]:
        """Extract references"""
        references = []

        if 'calls' not in self._queries:
            return references

        try:
            cursor = QueryCursor(self._queries['calls'])
            matches = cursor.matches(tree.root_node)

            for match in matches:
                pattern_index, captures_dict = match

                # Function calls
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

                # Method calls
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
            logger.error(f"Failed to extract references: {e}")

        return references

    def _extract_imports_native(self, tree, content: str) -> List[str]:
        """Extract import statements using tree-sitter"""
        imports = []

        if 'imports' not in self._queries:
            return imports

        try:
            cursor = QueryCursor(self._queries['imports'])
            captures = cursor.captures(tree.root_node)

            # import statements
            import_names = captures.get('import_name', [])
            for node in import_names:
                module_name = node.text.decode('utf-8').split('.')[0]
                if module_name:
                    imports.append(module_name)

            # from import statements
            from_modules = captures.get('from_module', [])
            for node in from_modules:
                module_name = node.text.decode('utf-8').split('.')[0]
                if module_name and not module_name.startswith('.'):
                    imports.append(module_name)

            # from import list statements
            from_module_lists = captures.get('from_module_list', [])
            for node in from_module_lists:
                module_name = node.text.decode('utf-8').split('.')[0]
                if module_name and not module_name.startswith('.'):
                    imports.append(module_name)

            # from import alias statements
            from_name_lists = captures.get('from_name_list', [])
            for node in from_name_lists:
                module_name = node.text.decode('utf-8').split('.')[0]
                if module_name:
                    imports.append(module_name)

        except Exception as e:
            logger.error(f"Failed to extract imports: {e}")

        return list(set(imports))  # Remove duplicates

    def _extract_exports_native(self, tree, content: str) -> List[str]:
        """Extract export statements using tree-sitter (__all__)"""
        exports = []

        try:
            # Find __all__ assignment
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
                # Iterate through list items
                for child in list_node.children:
                    if child.type == 'string':
                        export_name = child.text.decode('utf-8').strip('\'"')
                        exports.append(export_name)

        except Exception as e:
            logger.error(f"Failed to extract exports: {e}")

        return exports

    # ==============================
    # Helper Methods
    # ==============================

    def _extract_code_content(self, node, content: str) -> Optional[str]:
        """Extract complete code content corresponding to the node"""
        try:
            if not node:
                return None

            # Get node position in source code
            start_line = node.start_point[0]
            end_line = node.end_point[0]
            start_col = node.start_point[1]
            end_col = node.end_point[1]

            # Split file content into lines
            lines = content.split('\n')

            # Extract corresponding code lines
            if start_line == end_line:
                # Single line code
                if start_line < len(lines):
                    line = lines[start_line]
                    return line[start_col:end_col]
            else:
                # Multi-line code
                result_lines = []

                # First line (starting from start_col)
                if start_line < len(lines):
                    result_lines.append(lines[start_line][start_col:])

                # Middle lines (complete lines)
                for line_idx in range(start_line + 1, end_line):
                    if line_idx < len(lines):
                        result_lines.append(lines[line_idx])

                # Last line (ending at end_col)
                if end_line < len(lines):
                    result_lines.append(lines[end_line][:end_col])

                return '\n'.join(result_lines)

        except Exception as e:
            logger.debug(f"Failed to extract code content: {e}")

        return None

    def _extract_docstring_from_body(self, body_node, content: str) -> Optional[str]:
        """Extract docstring from function/class body"""
        try:
            # Find first expression statement
            for child in body_node.children:
                if child.type == 'expression_statement':
                    for expr in child.children:
                        if expr.type == 'string':
                            docstring = expr.text.decode('utf-8')
                            # Clean docstring format
                            docstring = docstring.strip('"""\'\'\'')
                            return docstring.strip()
        except Exception as e:
            logger.debug(f"Failed to extract docstring: {e}")
        return None

    def _extract_parameters(self, params_node) -> List[str]:
        """Extract function parameters"""
        parameters = []
        try:
            for child in params_node.children:
                if child.type == 'identifier':
                    param_name = child.text.decode('utf-8')
                    parameters.append(param_name)
                elif child.type == 'default_parameter':
                    # Default parameter, get first child node (parameter name)
                    for param_child in child.children:
                        if param_child.type == 'identifier':
                            param_name = param_child.text.decode('utf-8')
                            parameters.append(param_name)
                            break
                elif child.type == 'typed_parameter':
                    # Type-annotated parameter, get first child node (parameter name)
                    for param_child in child.children:
                        if param_child.type == 'identifier':
                            param_name = param_child.text.decode('utf-8')
                            parameters.append(param_name)
                            break
        except Exception as e:
            logger.debug(f"Failed to extract parameters: {e}")
        return parameters

    def _find_parent_class_name(self, method_node) -> Optional[str]:
        """Find parent class name of the method"""
        try:
            if not method_node:
                return None

            # Traverse upward to find class definition
            current = method_node.parent
            while current:
                if current.type == 'class_definition':
                    for child in current.children:
                        if child.type == 'identifier':
                            return child.text.decode('utf-8')
                current = current.parent
        except Exception as e:
            logger.debug(f"Failed to find parent class name: {e}")
        return None

    def _extract_class_signature(self, class_node, lines: List[str]) -> Optional[str]:
        """Extract class signature"""
        try:
            start_line = class_node.start_point[0]
            end_line = min(class_node.end_point[0], start_line + 2)  # Look at most 3 lines

            signature_lines = []
            for i in range(start_line, end_line + 1):
                if i < len(lines):
                    line = lines[i].strip()
                    signature_lines.append(line)
                    if line.endswith(':'):
                        break

            return ' '.join(signature_lines) if signature_lines else None
        except Exception as e:
            logger.debug(f"Failed to extract class signature: {e}")
            return None

    def _extract_function_signature(self, function_node, lines: List[str]) -> Optional[str]:
        """Extract function signature"""
        try:
            start_line = function_node.start_point[0]
            end_line = function_node.end_point[0]

            # Find end position of function definition (colon)
            for i in range(start_line, min(end_line + 1, start_line + 5)):
                if i < len(lines):
                    line = lines[i].strip()
                    if line.endswith(':'):
                        # Build signature
                        signature_lines = []
                        for j in range(start_line, i + 1):
                            if j < len(lines):
                                signature_lines.append(lines[j].strip())
                        return ' '.join(signature_lines)

            # If colon not found, return first line
            return lines[start_line].strip() if start_line < len(lines) else None
        except Exception as e:
            logger.debug(f"Failed to extract function signature: {e}")
            return None

    # ==============================
    # Compatibility Methods (for consistency with BaseParser interface)
    # ==============================

    def _get_default_query(self) -> str:
        """Return empty query for compatibility with BaseParser"""
        return ""

    def generate_skeleton(self, content: str, file_path: Path) -> str:
        """
        Generate Python code skeleton
        Optimized version: directly use tree-sitter to extract key structures
        """
        if not self._parser or not self._language:
            return f"# Parser not initialized\n# File: {file_path}\n"

        try:
            source_bytes = content.encode('utf-8')
            tree = self._parser.parse(source_bytes)

            if tree.root_node.has_error:
                logger.warning(f"Syntax errors found during skeleton generation: {file_path}")

            # Extract symbols
            symbols = self._extract_symbols(tree, content, file_path)

            # Generate skeleton
            lines = content.split('\n')
            skeleton_lines = []

            # Add file header
            skeleton_lines.append(f"# File: {file_path}")
            skeleton_lines.append("")

            # Sort symbols by line number
            symbols_by_line = {}
            for symbol in symbols:
                line = symbol.line_number
                if line not in symbols_by_line:
                    symbols_by_line[line] = []
                symbols_by_line[line].append(symbol)

            # Generate skeleton content
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
            logger.error(f"Failed to generate skeleton {file_path}: {e}")
            return f"# Skeleton generation failed: {e}\n# File: {file_path}\n"