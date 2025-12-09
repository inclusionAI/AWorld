# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import json
import random
import traceback
from typing import Dict, List, Optional, Any

from aworld.logs.util import logger
from aworld.models.llm import get_llm_model, call_llm_model
from train.data_gen.schema import TreeNode, ToolGenerateConfig


class ToolsTree:
    """Tool hierarchy structure, root (virtual node) -> category -> main capability (tool) -> sub capability (action, optional) -> tool spec."""

    def __init__(self, tool_gen_config: ToolGenerateConfig = None):
        self.root = TreeNode(name="root", description="tool root node", children={})
        # category -> nodes
        self.cate_nodes: Dict[str, TreeNode] = {}
        self.tool_gen_config = tool_gen_config or ToolGenerateConfig()

    async def build(self):
        meta_datas = await self.load_sources()
        for meta_data in meta_datas:
            cate_capable = await self._tool_category(meta_data)
            if cate_capable:
                self._add_cate_capable(meta_data)
            else:
                logger.warn(f"{meta_data} not category info.")
        logger.info("tools tree build finished.")

    async def load_sources(self) -> List[Dict]:
        """Load seed tool data from source paths."""
        if not self.tool_gen_config.source_paths:
            logger.error("no source paths provide, will use default data.")
            return [
                {
                    "category": "Financial Services",
                    "capabilities": [
                        "Stock price inquiry", "Exchange rate conversion", "Account balance inquiry",
                        "Transaction records", "Portfolio analysis", "Risk assessment"
                    ]
                },
                {
                    "category": "Transportation and Travel",
                    "capabilities": [
                        "Route planning", "Realtime traffic", "Public transportation inquiry",
                        "Taxi service", "Parking information", "Flight inquiry"
                    ]
                },
            ]

        meta_datas = []
        for path in self.tool_gen_config.source_paths:
            with open(path, "r+") as reader:
                line = reader.readline()
                line_json = json.loads(line)
                meta_datas.extend(line_json)
        return meta_datas

    async def _tool_category(self, meta_data: Dict[str, Any]) -> Optional[Dict]:
        """Obtain category and capabilities information of tool from a metadata json or using LLM"""
        if "category" in meta_data and "capabilities" in meta_data:
            return {
                "category": meta_data["category"],
                "capabilities": meta_data["capabilities"]
            }

        if "content" in meta_data:
            return await self._tool_category_from_llm(meta_data["content"])
        return None

    async def _tool_category_from_llm(self, document_content: str) -> Optional[Dict]:
        if not self.tool_gen_config.llm_config:
            return None

        system_prompt = """You are an expert in tool category analysis. Please extract tool related domain information and feature list from the given document content.

Please return JSON in the following format:
{
    "category": "",
    "capabilities": ["Function1", "Function2", "Function3"]
}

Requirements:
- Identify the main tool service domain involved in the document
- Extract specific tool capability points, and describe each capability in concise Chinese
- If the document is not related to the tool, return null
- The capability list should include 3-8 specific capability points

Please analyze the following document content and extract tool information, and return the result in JSON format.
"""

        user_prompt = document_content[:100000]

        messages = [
            {
                "role": "system", "content": system_prompt
            },
            {
                "role": "user", "content": user_prompt
            }
        ]

        try:
            llm_model = get_llm_model(self.tool_gen_config.llm_config)
            resp = call_llm_model(llm_model, messages=messages)

            import json
            result = json.loads(resp.content)

            if result and "category" in result and "capabilities" in result:
                return result
            else:
                return None
        except Exception as e:
            logger.error(f"Obtain category and capabilities fail. {traceback.format_exc()}")
            return None

    def _add_cate_capable(self, meta_data: Dict[str, Any]):
        category = meta_data["category"]
        capabilities = meta_data["capabilities"]

        cate_node = TreeNode(
            name=category,
            description=f"{category} tool category",
            children={}
        )

        # Add cate node as the root child
        self.root.add_child(cate_node)
        self.cate_nodes[category] = cate_node

        # Add capabilities as the category node children
        for ability in capabilities:
            capability_node = TreeNode(
                name=ability,
                description=f"{ability} ability",
                children={}
            )
            cate_node.add_child(capability_node)

            # Add sub-capabilities if needed
            sub_abilities = self._generate_sub_capabilities(ability, meta_data)
            for sub_ability in sub_abilities:
                sub_node = TreeNode(
                    name=sub_ability,
                    description=f"{sub_ability} sub ability",
                    children={}
                )
                capability_node.add_child(sub_node)

    def _generate_sub_capabilities(self, ability: str, meta_data: Dict[str, Any]) -> List[str]:
        """Generate sub-functionalities for a given functionality"""
        capabilities_dict = meta_data.get('sub_capabilities', {})
        if capabilities_dict:
            sub_capabilities = capabilities_dict.get(ability)
            if sub_capabilities:
                return sub_capabilities

        sub_capabilities_content = meta_data.get('sub_capabilities_content', {})
        if sub_capabilities_content:
            sub_content = sub_capabilities_content.get(ability)
            if sub_content:
                # llm generate
                pass

        return []

    def subtree(self, category: str = None, max_depth: int = 3) -> Optional[TreeNode]:
        """Get a subtree starting from a specific category with limited depth.

        Args:
            category: Get specific or random category subtree.
            max_depth: Maximum depth of the subtree.

        Returns:
            TreeNode representing the subtree root.
        """
        if category and category in self.cate_nodes:
            return self._extract_subtree(self.cate_nodes[category], max_depth)

        # Random sampling if no specific domain
        if self.cate_nodes:
            random_domain = random.choice(list(self.cate_nodes.values()))
            return self._extract_subtree(random_domain, max_depth)
        else:
            logger.warning('No category nodes to get subtree.')

        return None

    def _extract_subtree(self, node: TreeNode, max_depth: int) -> TreeNode:
        """Extract a subtree with limited depth"""
        if max_depth <= 0:
            return TreeNode(node.name, node.description, {})

        new_node = TreeNode(node.name, node.description, {})
        for child in node.children.values():
            if max_depth > 1:
                new_child = self._extract_subtree(child, max_depth - 1)
                new_node.add_child(new_child)

        return new_node

    def get_category(self, tree_node: TreeNode) -> str:
        """Get category from the tree node."""
        if tree_node.level == 1:
            return tree_node.name
        else:
            return self.get_category(tree_node.parent)

    def get_capabilities(self, tree_node: TreeNode) -> List[str]:
        """Get capabilities from the tree node."""
        capabilities = []

        def collect_capabilities(node: TreeNode):
            # level 0 is root, level 1 is category,
            if node.level == 2:
                capabilities.append(node.name)
            elif node.level > 2:
                return

            for child in node.children.values():
                collect_capabilities(child)

        collect_capabilities(tree_node)
        return capabilities

    def get_sub_capabilities(self, tree_node: TreeNode) -> Dict[str, List[str]]:
        """Get sub-capabilities from the tree node."""
        sub_capabilities = {}

        def collect_sub_capabilities(node: TreeNode):
            if node.level >= 3:
                sub_capabilities[node.parent.name].append(node.name)
            for child in node.children.values():
                collect_sub_capabilities(child)

        for child in tree_node.children.values():
            collect_sub_capabilities(child)
            sub_capabilities[child.parent.name] = list(set(sub_capabilities[child.parent.name]))
            logger.debug(f"{child.parent.name}: {sub_capabilities[child.parent.name]}")

        return sub_capabilities

    def save_tools_categories(self, file_path: str):
        """Save the tools tree to a JSON file"""
        tree_data = self._encode_node(self.root)
        with open(file_path, 'w+', encoding='utf-8') as f:
            json.dump(tree_data, f, ensure_ascii=False, indent=2)

    def load_tools_categories(self, file_path: str):
        """Load the tools tree from a json file.

        Args:
            file_path: Tools tree file.
        """
        with open(file_path, 'r+', encoding='utf-8') as reader:
            tree_data = json.load(reader)
        self.root = self._decode_node(tree_data)

        if self.cate_nodes:
            self.cate_nodes.clear()
        else:
            self.cate_nodes = {}
        for child in self.root.children.values():
            # only record cate node
            if child.level == 1:
                self.cate_nodes[child.name] = child

    def _encode_node(self, node: TreeNode, parent: TreeNode = None) -> Dict[str, Any]:
        return {
            "name": node.name,
            "description": node.description,
            "level": node.level,
            "children": {name: self._encode_node(child)
                         for name, child in node.children.items()}
        }

    def _decode_node(self, data: Dict[str, Any], parent: TreeNode = None) -> TreeNode:
        node = TreeNode(
            name=data["name"],
            description=data["description"],
            children={},
            parent=parent,
            level=data.get("level", 0)
        )

        for child_data in data.get("children", {}).values():
            child = self._decode_node(child_data, node)
            node.children[child.name] = child

        return node

    def tree_info(self) -> Dict:
        """The tools tree statistics info."""
        total_nodes = len(self.root.get_all_descendants()) + 1
        category_count = len(self.cate_nodes)
        capability_total_count = sum(len(cate_node.children) for cate_node in self.cate_nodes.values())
        capability_count = {cate: len(node.children) for cate, node in self.cate_nodes.items()}

        return {
            "total_nodes": total_nodes,
            "category_count": category_count,
            "capability_total_count": capability_total_count,
            "cate_capability_count": capability_count,
            "depth": self.depth()
        }

    def depth(self) -> int:
        """Get the depth of the tree."""

        def get_depth(node: TreeNode) -> int:
            if not node.children:
                return node.level
            return max(get_depth(child) for child in node.children.values())

        return get_depth(self.root)
