"""
AWorld AST Framework - Data Models
=================================

Defines core data structures and models used in the AST analysis framework.
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Set, Optional, Any, Tuple


class SymbolType(Enum):
    """Symbol type enumeration"""
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    VARIABLE = "variable"
    IMPORT = "import"
    MODULE = "module"
    CONSTANT = "constant"
    INTERFACE = "interface"
    PROPERTY = "property"


class ReferenceType(Enum):
    """Reference type enumeration"""
    CALL = "call"
    INHERITANCE = "inheritance"
    IMPORT = "import"
    ASSIGNMENT = "assignment"
    ACCESS = "access"
    DEFINITION = "definition"


@dataclass
class Symbol:
    """Code symbol definition"""
    name: str
    symbol_type: SymbolType
    file_path: Path
    line_number: int
    column: int
    end_line: int = 0
    end_column: int = 0
    signature: Optional[str] = None
    docstring: Optional[str] = None
    content: Optional[str] = None  # Complete code content of the symbol
    parent: Optional[str] = None  # Parent symbol name
    modifiers: Set[str] = field(default_factory=set)  # public, private, static, etc.
    parameters: List[str] = field(default_factory=list)
    return_type: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def full_name(self) -> str:
        """Get full symbol name"""
        if self.parent:
            return f"{self.parent}.{self.name}"
        return self.name

    @property
    def location_key(self) -> str:
        """Get location key for caching and indexing"""
        return f"{self.file_path}:{self.line_number}:{self.column}"


@dataclass
class Reference:
    """Code reference"""
    symbol_name: str
    reference_type: ReferenceType
    file_path: Path
    line_number: int
    column: int
    context: Optional[str] = None  # Context code of the reference
    target_symbol: Optional[Symbol] = None  # Referenced symbol
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def location_key(self) -> str:
        """Get location key"""
        return f"{self.file_path}:{self.line_number}:{self.column}"


@dataclass
class CodeNode:
    """Code node for building code relationship graph"""
    file_path: Path
    symbols: List[Symbol] = field(default_factory=list)
    references: List[Reference] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    exports: List[str] = field(default_factory=list)
    dependencies: Set[Path] = field(default_factory=set)
    dependents: Set[Path] = field(default_factory=set)
    weight: float = 1.0  # PageRank weight
    last_modified: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_symbol(self, symbol: Symbol) -> None:
        """Add symbol"""
        self.symbols.append(symbol)

    def add_reference(self, reference: Reference) -> None:
        """Add reference"""
        self.references.append(reference)

    def get_symbols_by_type(self, symbol_type: SymbolType) -> List[Symbol]:
        """Get symbols by type"""
        return [s for s in self.symbols if s.symbol_type == symbol_type]

    def get_references_by_type(self, ref_type: ReferenceType) -> List[Reference]:
        """Get references by type"""
        return [r for r in self.references if r.reference_type == ref_type]


@dataclass
class LogicLayer:
    """L1 - Panoramic logic layer data structure"""
    project_structure: Dict[str, Any]  # Project directory structure
    key_symbols: List[Symbol]  # Key symbol table
    call_graph: Dict[str, List[str]]  # Call relationship graph
    dependency_graph: Dict[Path, Set[Path]]  # Dependency relationship graph
    execution_heatmap: Dict[str, int] = field(default_factory=dict)  # Execution heatmap
    module_descriptions: Dict[Path, str] = field(default_factory=dict)  # Module descriptions

    def to_markdown(self) -> str:
        """Convert to Markdown format description"""
        md_lines = ["# Project Logic Structure", ""]

        # Project structure
        md_lines.extend(["## Project Structure", "```"])
        md_lines.append(self._format_structure(self.project_structure))
        md_lines.extend(["```", ""])

        # Key symbols
        md_lines.extend(["## Key Symbols", ""])
        for symbol in sorted(self.key_symbols, key=lambda s: s.name):
            heat = self.execution_heatmap.get(symbol.full_name, 0)
            heat_indicator = "🔥" * min(heat // 10, 5) if heat > 0 else ""
            md_lines.append(f"- **{symbol.full_name}** ({symbol.symbol_type.value}) {heat_indicator}")
            if symbol.docstring:
                md_lines.append(f"  - {symbol.docstring.split('.')[0]}.")

        md_lines.append("")

        # Call relationships
        md_lines.extend(["## Call Relationships", ""])
        for caller, callees in self.call_graph.items():
            if callees:
                md_lines.append(f"- **{caller}** → {', '.join(callees)}")

        return "\n".join(md_lines)

    def _format_structure(self, structure: Dict, indent: int = 0) -> str:
        """Format directory structure"""
        lines = []
        prefix = "  " * indent
        for key, value in structure.items():
            if isinstance(value, dict):
                lines.append(f"{prefix}{key}/")
                lines.append(self._format_structure(value, indent + 1))
            else:
                lines.append(f"{prefix}{key}")
        return "\n".join(lines)


@dataclass
class SkeletonLayer:
    """L2 - Interface skeleton layer data structure"""
    file_skeletons: Dict[Path, str]  # File skeleton code
    symbol_signatures: Dict[str, str]  # Symbol signature mapping
    line_mappings: Dict[Path, Dict[int, int]]  # Line number mapping (skeleton to original)

    def get_skeleton(self, file_path: Path) -> Optional[str]:
        """Get file skeleton"""
        return self.file_skeletons.get(file_path)

    def get_signature(self, symbol_name: str) -> Optional[str]:
        """Get symbol signature"""
        return self.symbol_signatures.get(symbol_name)


@dataclass
class ImplementationLayer:
    """L3 - Source code implementation layer data structure"""
    code_nodes: Dict[Path, 'CodeNode']  # Complete code nodes, including symbols and content

    def get_code_node(self, file_path: Path) -> Optional['CodeNode']:
        """Get code node"""
        return self.code_nodes.get(file_path)

    def get_symbol_by_name(self, symbol_name: str) -> Optional['Symbol']:
        """Get symbol by name"""
        for node in self.code_nodes.values():
            for symbol in node.symbols:
                if symbol.name == symbol_name or symbol.full_name == symbol_name:
                    return symbol
        return None

    def get_symbols_in_file(self, file_path: Path) -> List['Symbol']:
        """Get all symbols in file"""
        node = self.code_nodes.get(file_path)
        return node.symbols if node else []


@dataclass
class RepositoryMap:
    """Complete repository mapping, including three-layer structure"""
    logic_layer: LogicLayer
    skeleton_layer: SkeletonLayer
    implementation_layer: ImplementationLayer
    code_nodes: Dict[Path, CodeNode]
    pagerank_scores: Dict[Path, float] = field(default_factory=dict)
    trajectory_mapping: Dict[str, List[Tuple[Path, int]]] = field(default_factory=dict)
    last_updated: Optional[float] = None

    def get_relevant_files(self,
                          user_mentions: List[str] = None,
                          trajectory_filter: bool = False,
                          top_k: int = 10) -> List[Path]:
        """Get relevant file list"""
        scores = self.pagerank_scores.copy()

        # If user mentions content, increase weight
        if user_mentions:
            for file_path, node in self.code_nodes.items():
                for symbol in node.symbols:
                    if any(mention.lower() in symbol.name.lower() for mention in user_mentions):
                        scores[file_path] = scores.get(file_path, 0.0) + 10.0

        # If trajectory filter is enabled, only return executed files
        if trajectory_filter and self.trajectory_mapping:
            executed_files = set()
            for locations in self.trajectory_mapping.values():
                executed_files.update(loc[0] for loc in locations)
            scores = {f: s for f, s in scores.items() if f in executed_files}

        # Sort by score and return top-k
        sorted_files = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [file_path for file_path, _ in sorted_files[:top_k]]

    def generate_context(self,
                        max_tokens: int = 8000,
                        user_mentions: List[str] = None,
                        trajectory_filter: bool = False) -> str:
        """Generate context for LLM"""
        context_parts = []

        # L1 - Panoramic logic layer
        context_parts.append("# Project Overview")
        context_parts.append(self.logic_layer.to_markdown())
        context_parts.append("")

        # Get relevant files
        relevant_files = self.get_relevant_files(user_mentions, trajectory_filter)

        # L2 - Interface skeleton layer
        context_parts.append("# Code Structure")
        for file_path in relevant_files[:5]:  # Limit file count
            skeleton = self.skeleton_layer.get_skeleton(file_path)
            if skeleton:
                context_parts.append(f"## {file_path}")
                context_parts.append(f"```python")
                context_parts.append(skeleton)
                context_parts.append("```")
                context_parts.append("")

        # TODO: Implement token counting and truncation logic
        return "\n".join(context_parts)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize RepositoryMap to dictionary (JSON serializable)"""
        def serialize_value(value: Any) -> Any:
            """Recursively serialize value"""
            if isinstance(value, Path):
                return str(value)
            elif isinstance(value, Enum):
                return value.value
            elif isinstance(value, (Symbol, Reference, CodeNode, LogicLayer, SkeletonLayer, ImplementationLayer)):
                # Handle dataclass objects
                result = {}
                for field_name, field_value in value.__dict__.items():
                    result[field_name] = serialize_value(field_value)
                    return result
            elif isinstance(value, dict):
                # Handle dictionary, convert Path keys to strings, ensure all keys are serializable
                result = {}
                for k, v in value.items():
                    # Ensure keys are string type (JSON requirement)
                    if isinstance(k, Path):
                        key = str(k)
                    elif isinstance(k, (int, float, bool)) or k is None:
                        key = k  # JSON supports these types as keys
                    else:
                        key = str(k)  # Convert other types to strings as well
                    result[key] = serialize_value(v)
                return result
            elif isinstance(value, (list, tuple)):
                return [serialize_value(item) for item in value]
            elif isinstance(value, set):
                return [serialize_value(item) for item in value]
            else:
                return value

        return {
            'logic_layer': serialize_value(self.logic_layer),
            'skeleton_layer': serialize_value(self.skeleton_layer),
            'implementation_layer': serialize_value(self.implementation_layer),
            'code_nodes': serialize_value(self.code_nodes),
            'pagerank_scores': serialize_value(self.pagerank_scores),
            'trajectory_mapping': serialize_value(self.trajectory_mapping),
            'last_updated': self.last_updated
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'RepositoryMap':
        """Deserialize from dictionary to RepositoryMap object"""
        def deserialize_value(value: Any, target_type: type = None) -> Any:
            """Recursively deserialize value"""
            if isinstance(value, dict):
                # Check if it's a known dataclass type
                if 'name' in value and 'symbol_type' in value and 'file_path' in value:
                    # Symbol
                    return Symbol(
                        name=value['name'],
                        symbol_type=SymbolType(value['symbol_type']),
                        file_path=Path(value['file_path']),
                        line_number=value['line_number'],
                        column=value['column'],
                        end_line=value.get('end_line', 0),
                        end_column=value.get('end_column', 0),
                        signature=value.get('signature'),
                        docstring=value.get('docstring'),
                        content=value.get('content'),
                        parent=value.get('parent'),
                        modifiers=set(value.get('modifiers', [])),
                        parameters=value.get('parameters', []),
                        return_type=value.get('return_type'),
                        metadata=value.get('metadata', {})
                    )
                elif 'symbol_name' in value and 'reference_type' in value and 'file_path' in value:
                    # Reference
                    return Reference(
                        symbol_name=value['symbol_name'],
                        reference_type=ReferenceType(value['reference_type']),
                        file_path=Path(value['file_path']),
                        line_number=value['line_number'],
                        column=value['column'],
                        context=value.get('context'),
                        target_symbol=deserialize_value(value.get('target_symbol'), Symbol) if value.get('target_symbol') else None,
                        metadata=value.get('metadata', {})
                    )
                elif 'file_path' in value and 'symbols' in value:
                    # CodeNode
                    return CodeNode(
                        file_path=Path(value['file_path']),
                        symbols=[deserialize_value(s, Symbol) for s in value.get('symbols', [])],
                        references=[deserialize_value(r, Reference) for r in value.get('references', [])],
                        imports=value.get('imports', []),
                        exports=value.get('exports', []),
                        dependencies={Path(p) for p in value.get('dependencies', [])},
                        dependents={Path(p) for p in value.get('dependents', [])},
                        weight=value.get('weight', 1.0),
                        last_modified=value.get('last_modified'),
                        metadata=value.get('metadata', {})
                    )
                elif 'project_structure' in value and 'key_symbols' in value:
                    # LogicLayer
                    return LogicLayer(
                        project_structure=deserialize_value(value.get('project_structure', {})),
                        key_symbols=[deserialize_value(s, Symbol) for s in value.get('key_symbols', [])],
                        call_graph=deserialize_value(value.get('call_graph', {})),
                        dependency_graph={Path(k): {Path(p) for p in v} 
                                         for k, v in value.get('dependency_graph', {}).items()},
                        execution_heatmap=value.get('execution_heatmap', {}),
                        module_descriptions={Path(k): v for k, v in value.get('module_descriptions', {}).items()}
                    )
                elif 'file_skeletons' in value:
                    # SkeletonLayer
                    return SkeletonLayer(
                        file_skeletons={Path(k): v for k, v in value.get('file_skeletons', {}).items()},
                        symbol_signatures=value.get('symbol_signatures', {}),
                        line_mappings={Path(k): v for k, v in value.get('line_mappings', {}).items()}
                    )
                elif 'code_nodes' in value and len(value) == 1:
                    # ImplementationLayer (new structure)
                    return ImplementationLayer(
                        code_nodes={Path(k): deserialize_value(v, CodeNode) for k, v in value.get('code_nodes', {}).items()}
                    )
                elif 'file_contents' in value:
                    # ImplementationLayer (old structure, backward compatible)
                    return ImplementationLayer(
                        code_nodes={}  # Old structure converted to new structure, but no code_nodes data
                    )
                else:
                    # Regular dictionary, try to convert string keys back to Path (if it looks like a path)
                    result = {}
                    for k, v in value.items():
                        # If key is string and looks like a path, try to convert to Path
                        if isinstance(k, str) and ('/' in k or '\\' in k or k.endswith('.py')):
                            try:
                                result[Path(k)] = deserialize_value(v)
                            except:
                                result[k] = deserialize_value(v)
                        else:
                            result[k] = deserialize_value(v)
                    return result
            elif isinstance(value, list):
                return [deserialize_value(item) for item in value]
            elif isinstance(value, str):
                # Check if it's a path string
                if '/' in value or '\\' in value or value.endswith('.py'):
                    try:
                        return Path(value)
                    except:
                        return value
                return value
            else:
                return value

        return cls(
            logic_layer=deserialize_value(data['logic_layer'], LogicLayer),
            skeleton_layer=deserialize_value(data['skeleton_layer'], SkeletonLayer),
            implementation_layer=deserialize_value(data['implementation_layer'], ImplementationLayer),
            code_nodes={Path(k): deserialize_value(v, CodeNode) for k, v in data.get('code_nodes', {}).items()},
            pagerank_scores={Path(k): v for k, v in data.get('pagerank_scores', {}).items()},
            trajectory_mapping={k: [(Path(path), line) for path, line in v] 
                               for k, v in data.get('trajectory_mapping', {}).items()},
            last_updated=data.get('last_updated')
        )