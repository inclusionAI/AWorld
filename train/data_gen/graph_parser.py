# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.logs.util import logger


class ExecutionGraph:
    """Parse graph-structured data.

    Two situations: directly building topology by LLM and building topology through IO (workflow).
    """

    @staticmethod
    async def parse(graph_str: str) -> tuple | list:
        """Parse the execution graph string and return nested tuple and list structures.

        () represents serial execution -> tuple
        [] represents parallel execution -> list

        Examples:
        graph_str: "(a, [b, c], d)"
        return: ("a", ["b", "c"], "d")

        Args:
            graph_str: Executed string

        Returns:
            Nested tuple/list hybrid structure
        """
        graph_str = graph_str.strip()

        def parse_recursive(s: str, pos: int = 0) -> (tuple | list, int):
            """Recursive parsing execution diagram, and return (parsing result, next location)."""
            s = s.strip()

            while pos < len(s) and s[pos].isspace():
                pos += 1

            # serial execution
            if pos < len(s) and s[pos] == '(':
                pos += 1
                items = []

                while pos < len(s):
                    # skip whitespace and commas
                    while pos < len(s) and (s[pos].isspace() or s[pos] == ','):
                        pos += 1

                    if pos >= len(s):
                        break

                    if s[pos] == ')':
                        pos += 1
                        return tuple(items), pos

                    item, pos = parse_recursive(s, pos)
                    items.append(item)
            # parallel execution
            elif pos < len(s) and s[pos] == '[':
                pos += 1
                items = []

                while pos < len(s):
                    # skip whitespace and commas
                    while pos < len(s) and (s[pos].isspace() or s[pos] == ','):
                        pos += 1

                    if pos >= len(s):
                        break

                    if s[pos] == ']':
                        pos += 1
                        return items, pos

                    item, pos = parse_recursive(s, pos)
                    items.append(item)
            # content
            else:
                start = pos
                while pos < len(s) and s[pos] not in '(),[],' and not s[pos].isspace():
                    pos += 1

                if start < pos:
                    return s[start:pos], pos

            return None, pos

        result, _ = parse_recursive(graph_str)
        return result

    @staticmethod
    async def traverse(graph: tuple | list | str) -> None:
        """Recursive traversal of execution graph structure for printing detailed hierarchical.

        Args:
            graph: The parsed execution graph structure（tuple/list）
        """

        def traverse_recursive(graph: tuple | list | str, cons: list, depth: int = 0) -> (tuple | list, int):
            # depth: current recursive depth
            indent = "  " * depth

            if isinstance(graph, tuple):
                cons.append(f"{indent}[Serial] Execution (tuple):")
                for i, item in enumerate(graph):
                    if isinstance(item, (tuple, list)):
                        cons.append(f"{indent}  ├─ [{i}] ")
                        traverse_recursive(item, cons, depth + 2)
                    else:
                        cons.append(f"{indent}  ├─ [{i}] {item}")

            elif isinstance(graph, list):
                cons.append(f"{indent}[Parallel] Execution (list):")
                for i, item in enumerate(graph):
                    if isinstance(item, (tuple, list)):
                        cons.append(f"{indent}  ├─ [{i}] ")
                        traverse_recursive(item, cons, depth + 2)
                    else:
                        cons.append(f"{indent}  ├─ [{i}] {item}")

            else:
                cons.append(f"{indent}Entity: '{graph}'")

        cons = []
        traverse_recursive(graph, cons)
        res = "\n".join(cons)
        logger.info(f'{res}')

    @staticmethod
    async def collect_entity(graph: tuple | list | str) -> list:
        """Collect all entities from the execution graph.

        Args:
            graph: The parsed execution graph structure

        Returns:
            List containing all entities
        """
        entities = []

        if isinstance(graph, (tuple, list)):
            for item in graph:
                entities.extend(await ExecutionGraph.collect_entity(item))
        else:
            entities.append(graph)

        return list(set(entities))

    @staticmethod
    async def analyze(graph: tuple | list | str, depth: int = 0) -> dict:
        """Detailed analysis of the structure and characteristics of the execution graph.

        Args:
            graph: The parsed execution graph structure
            depth: Current recursive depth

        Returns:
            Execution graph analysis information dict
        """
        analysis = {
            "type": "serial" if isinstance(graph, tuple) else "parallel" if isinstance(graph, list) else "entity",
            "depth": depth,
            "children": [],
            "entity_count": 0,
            "structure": None
        }

        if isinstance(graph, tuple):
            analysis["structure"] = f"({', '.join(str(g) for g in graph)})"
            for item in graph:
                child_analysis = await ExecutionGraph.analyze(item, depth + 1)
                analysis["children"].append(child_analysis)
                analysis["entity_count"] += child_analysis["entity_count"]

        elif isinstance(graph, list):
            analysis["structure"] = f"[{', '.join(str(g) for g in graph)}]"
            for item in graph:
                child_analysis = await ExecutionGraph.analyze(item, depth + 1)
                analysis["children"].append(child_analysis)
                analysis["entity_count"] += child_analysis["entity_count"]

        else:
            analysis["structure"] = graph
            analysis["entity_count"] = 1

        return analysis
