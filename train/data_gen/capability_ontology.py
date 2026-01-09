# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import json
import random
import traceback
from typing import Dict, List, Optional, Any

from aworld.logs.util import logger
from aworld.models.llm import get_llm_model, call_llm_model
from train.data_gen.schema import TreeNode, OntologyConfig


class CapabilityOntology:
    """Capability ontology formally models what tools or agents can do, defining functions, properties, and relationships to enable automation and flexible integration.

    Capability hierarchy structure:
    - root (virtual node) ->
        - category (domain) ->
            - main capability (swarm or agent or tool) ->
                - sub capability (agent or action, optional)
                    - sub ...
    """

    def __init__(self, config: OntologyConfig = None):
        self.root = TreeNode(name="root", description="root node", children={})
        # category -> nodes
        self.cate_nodes: Dict[str, TreeNode] = {}
        self.config = config or OntologyConfig()
        self.task = self.config.task

    async def build(self):
        meta_datas = await self.load_sources()
        for meta_data in meta_datas:
            cate_capable = await self._category(meta_data)
            if cate_capable:
                self._add_cate_capable(meta_data)
            else:
                logger.warn(f"{meta_data} not category info.")
        logger.info("capability ontology build finished.")

    async def load_sources(self) -> List[Dict]:
        """Load capability data from source paths."""
        default_sources = [{
            "category": "Local Life Services",
            "capabilities": ["Food & Dining", "Home Services", "Community Events", "Local Shopping", "Transportation",
                             "Healthcare Access", "Recreation & Leisure"],
            "sub_capabilities": {
                "Food & Dining": ["Search restaurants", "Order food delivery", "Book table", "Review restaurant",
                                  "View menu"],
                "Home Services": ["Book cleaning service", "Request repair", "Schedule maintenance", "Hire babysitter",
                                  "Cancel service"],
                "Community Events": ["Browse events", "Register for event", "Share event", "Create event",
                                     "Cancel registration"],
                "Local Shopping": ["Search local stores", "Order product", "Reserve item", "Track delivery",
                                   "Review store"],
                "Transportation": ["Book taxi", "Check bus schedule", "Rent bike", "Track ride", "Cancel booking"],
                "Healthcare Access": ["Find nearby clinic", "Book doctor appointment", "Order medicine",
                                      "Consult pharmacist", "View health tips"],
                "Recreation & Leisure": ["Search parks", "Book sports facility", "Join club", "View activity schedule",
                                         "Cancel booking"]
            }
        }]
        if not self.config.source_paths:
            if not self.config.llm_config:
                logger.error("no source paths and llm config provide, will use default data.")
                return default_sources
            else:
                prompt = """You are a senior domain modeling architect and intent classification expert. Your task is to analyze user inputs (task descriptions, system requirements, or mixed instructions) and construct a **three-level hierarchical** functional classification system.

# Hierarchy Definitions
1.  **Category**: Macro industry or business domain boundaries (e.g., "Financial Services", "Smart Home").
2.  **Capability**: The core functional modules or service groups within this domain (e.g., "Stock Trading", "Lighting Control").
3.  **Sub-capability**: The specific executable atomic operations/intentions under this module (e.g., "Buy stock", "Dim the lights").

# Output Format
Output a list of JSON Object with the following structure:
```json
[
    {
        "category": "Domain Name",
        "capabilities": ["Capability_A", "Capability_B"],
        "sub_capabilities": {
            "Capability_A": ["Atomic Action 1", "Atomic Action 2"],
            "Capability_B": ["Atomic Action 3", "Atomic Action 4"]
        }
    }
]

# Rules
1. **Decomposition**:
    - If the input is a large topic (such as "Smart Home Solution"), please break it down into subsystems (such as "Lighting Control", "Security", "Appliance Management").
    - If the input encompasses multiple unrelated domains, generate multiple Objects to describe them respectively.
2. **Consistency**: 
    - The Key in the sub_capabilities object must strictly match the strings in the capabilities list.
    - Do not create Sub-capabilities without corresponding Key.
3. **Granularity**:
    - **Category**: Must be a specific noun phrase representing an independent domain module.
    - **Capability** It should be a noun phrase (functional module).
    - **Sub-capability** It should be a phrase in the "Verb + Noun" structure, indicating a specific action.
4. **Coverage**:
    - List 3-10 core Capability for each category.
    - The Capability points should cover the main use cases of the module.
    - List at least 3-5 sub-capabilities under each capability, covering CRUD operations or core business processes.
    - The functional points should cover the main use cases of the module.
5. **Format**: Directly output a JSON list without including Markdown tags or other explanatory text.

# Few-Shot Examples

## Example 1: Broad Topic Decomposition
**Input**: "Design the core functions of an e-commerce platform"
**Output**:
```json
[
    {
        "category": "E-commerce Platform",
        "capabilities": ["Product Search", "Cart Management", "Order Management", "Inventory Management", "User Management"],
        "sub_capabilities": {
            "Product Search": ["Search by keyword", "Filter by price", "View product details", "Check reviews"],
            "Cart Management": ["Add to cart", "Remove from cart", "Update quantity", "Clear cart"],
            "Order Processing": ["Checkout", "Apply coupon", "Select shipping address"],
            "Order Management": ["Create order", "Cancel order", "Track shipment", "Process refund", "View order history"],
            "Inventory Management": ["Add product", "Update stock level", "Remove product", "Check low stock", "Manage suppliers"]
            "User Management": ["Register user", "Reset password", "Ban user", "Update profile", "Assign roles"]
        }
    }
]
```
## Example 2: Mixed User Intent
**Input**: "Help me handle the bank transfer, book a ticket to Shanghai, and remind me of the meeting tomorrow morning."
**Output**:
```json
[
    {
        "category": "Financial Services",
        "capabilities": ["Fund Transfer"],
        "sub_capabilities": {
            "Fund Transfer": ["Check balance", "Transfer money", "View transaction history", "Add beneficiary"]
        }
    },
    {
        "category": "Travel & Transportation",
        "capabilities": ["Flight Booking"],
        "sub_capabilities": {
            "Flight Booking": ["Search flights", "Book ticket", "Check flight status", "Cancel booking"]
        }
    },
    {
        "category": "Personal Efficiency",
        "capabilities": ["Schedule Management"],
        "sub_capabilities": {
            "Schedule Management": ["Create event", "Set reminder", "List upcoming events", "Delete reminder"]
        }
    }
]
```

## Example 3: Specific Domain Expansion
**Input**: "Healthcare App"
**Output**:
```json
[
    {
        "category": "Healthcare",
        "capabilities": ["Appointment Scheduling", "Telemedicine", "Health Tracking"]
        "sub_capabilities": {
            "Appointment Scheduling": ["Book doctor appointment", "Reschedule visit", "Cancel appointment", "Find nearby clinic"]
            "Telemedicine": ["Start video consultation", "Chat with doctor", "Upload medical report", "Download prescription"]
            "Health Tracking": ["Log heart rate", "Track sleep patterns", "Input blood pressure", "View health trends"]
        }
    }
]
```
"""
                messages = [
                    {
                        "role": "system", "content": prompt
                    },
                    {
                        "role": "user", "content": self.task
                    }
                ]

                try:
                    llm_model = get_llm_model(self.config.llm_config)
                    resp = call_llm_model(llm_model, messages=messages)
                    resp.content = resp.content.replace("```json", "").replace("```", "")
                    results: list = json.loads(resp.content)

                    if results:
                        idxs = []
                        for idx, result in enumerate(results):
                            if "category" in result and "capabilities" in result:
                                continue
                            logger.warning(f"Invalid result: {result}")
                            idxs.append(idx)
                        [results.pop(idx) for idx in idxs]
                        return results
                    else:
                        logger.warning(f"No results found, please check your input.")
                        return default_sources
                except Exception as e:
                    logger.error(f"Obtain category and capabilities fail. {traceback.format_exc()}")
                    return default_sources

        meta_datas = []
        for path in self.config.source_paths:
            with open(path, "r+") as reader:
                line = reader.readline()
                line_json = json.loads(line)
                meta_datas.extend(line_json)
        return meta_datas

    async def _category(self, meta_data: Dict[str, Any]) -> Optional[Dict]:
        """Obtain category and capabilities information from a metadata json or using LLM"""
        if "category" in meta_data and "capabilities" in meta_data:
            return meta_data

        if "content" in meta_data:
            return await self._category_from_llm(meta_data["content"])
        return None

    async def _category_from_llm(self, content: str) -> Optional[Dict]:
        if not self.config.llm_config:
            return None

        system_prompt = """You are an expert in category analysis. Please extract related domain information and feature list from the given document content.

Please return JSON in the following format:
{
    "category": "",
    "capabilities": ["ability1", "ability2", "ability3"]
}

Requirements:
- Identify the main capability service domain involved in the document
- Extract specific capability points, and describe each capability in concise manner
- If the content is not related to the category or main ability, return null
- The capability list should include 3-8 specific capability points

Please analyze the following document content and extract capability information, and return the result in JSON format.
"""

        user_prompt = content[:100000]

        messages = [
            {
                "role": "system", "content": system_prompt
            },
            {
                "role": "user", "content": user_prompt
            }
        ]

        try:
            llm_model = get_llm_model(self.config.llm_config)
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
            description=f"{category} category",
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
        if tree_node.name == 'root':
            return ''

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

    def save_categories(self, file_path: str):
        """Save the capability ontology tree to a JSON file"""
        tree_data = self._encode_node(self.root)
        with open(file_path, 'w+', encoding='utf-8') as f:
            json.dump(tree_data, f, ensure_ascii=False, indent=2)

    def load_categories(self, file_path: str):
        """Load the capability ontology tree from a json file."""
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
        """The capability ontology tree statistics info."""
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
