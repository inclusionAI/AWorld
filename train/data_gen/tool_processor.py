# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import random
from typing import Dict, List

from aworld.logs.util import logger
from train.data_gen.schema import Diversity, ToolSpec, Complexity, TreeNode
from train.data_gen.tool_tree import ToolsTree


class ToolProcessor:
    """Operator calculation operation of tools based on tool tree structure."""
    def __init__(self, tool_tree: ToolsTree):
        self.tool_tree = tool_tree
        self.capabilities_hash = set()

    async def samples(self,
                      category: str = None,
                      complexity_distribution: Dict[str, float] = None,
                      count: int = 5):
        """Sample a subtree from the context tree and create API specification

        Args:
            category: Tool category, random value if None.
            complexity_distribution: Distribution of complexity sample.
            count: Sample category node of tool tree count.

        Returns:
            List of sampled tools spec
        """
        if not complexity_distribution:
            complexity_distribution = Diversity.default_distribute()

        tool_specs = []
        for _ in range(count):
            complexity = await self._distribute_choice(complexity_distribution)
            tool_spec = await self.sample(category=category, complexity=complexity)
            await self._tool_capabilities_unique(tool_spec)
            tool_specs.append(tool_spec)

        return tool_specs

    async def sample(self, category: str = None, complexity: str = None) -> ToolSpec:
        """Tool sampling based on complexity and category."""
        if not complexity:
            complexity = self._distribute_choice(Complexity.default_distribute())

        return await self.sample_by_complexity(category, complexity)

    async def sample_by_complexity(self,
                                   category: str = None,
                                   complexity: str = None,
                                   min_abilities_count: int = 2,
                                   max_abilities_count: int = 5,
                                   min_category_count: int = 1,
                                   max_category_count: int = 3) -> ToolSpec:
        if complexity == Complexity.HIGH:
            return await self._sample_high_complexity(category,
                                                      min_category_count,
                                                      max_category_count,
                                                      min_abilities_count,
                                                      max_abilities_count)
        elif complexity == Complexity.MEDIUM:
            return await self._sample_medium_complexity(category, min_abilities_count, max_abilities_count)
        else:
            return await self._sample_low_complexity(category)

    async def _distribute_choice(self, distribution: Dict[str, float]) -> str:
        """Make a weighted random choice of choices."""
        choices = list(distribution.keys())
        weights = list(distribution.values())
        return random.choices(choices, weights=weights)[0]

    async def _sample_low_complexity(self, category: str = None) -> ToolSpec:
        """Sample for low complexity, means tool with single capability of category."""
        cate = await self._select_category(category)
        if cate is None:
            return self._fallback_tool_spec('', Diversity.LOW, Complexity.LOW)

        cate_node = self.tool_tree.cate_nodes[cate]
        if cate_node.children:
            ability = random.choice(list(cate_node.children.keys()))
            ability_node = cate_node.children[ability]

            subtree = TreeNode(name=cate, description=f"{cate} category", children={})
            node = TreeNode(name=ability, description=ability_node.description, children={})
            subtree.add_child(node)

            return ToolSpec(
                name=f"{ability}_tool",
                description="",
                category=category,
                capabilities=[ability],
                diversity=Diversity.LOW,
                complexity=Complexity.LOW,
                tree_node=subtree
            )

        return self._fallback_tool_spec(cate, Diversity.LOW, Complexity.LOW)

    async def _sample_medium_complexity(self,
                                        category: str = None,
                                        min_abilities_count: int = 2,
                                        max_abilities_count: int = 4,
                                        add_prefix: bool = False) -> ToolSpec:
        """Sample for medium complexity, means tool with multiple (config num) related capabilities and sub capabilities."""
        cate = await self._select_category(category)
        if cate is None:
            return self._fallback_tool_spec('', Diversity.LOW, Complexity.LOW)
        cate_node = self.tool_tree.cate_nodes[cate]

        capabilities = list(cate_node.children.keys())
        capabilities_len = len(capabilities)
        if capabilities_len <= min_abilities_count + 1:
            selected = capabilities
        else:
            selected_num = min(random.randint(min_abilities_count, min(max_abilities_count, capabilities_len)),
                               capabilities_len)
            selected = random.sample(capabilities, selected_num)

        # Create subtree with selected capabilities
        subtree = TreeNode(name=cate, description=f"{cate} category", children={})
        for ability in selected:
            # must in cate_node children
            ability_node = cate_node.children[ability]
            if add_prefix:
                ability = f"{cate}_{ability}"
            new_node = TreeNode(name=ability, description=ability_node.description, children={})

            # Include some sub-capabilities for complexity
            sub_capabilities = list(ability_node.children.keys())
            if sub_capabilities:
                sub_capabilities_len = len(sub_capabilities)
                sub_selected_num = min(random.randint(1, min(sub_capabilities_len, max_abilities_count)),
                                       sub_capabilities_len)
                sub_selected = random.sample(sub_capabilities, sub_selected_num)
                for sub_ability in sub_selected:
                    sub_ability_node = ability_node.children[sub_ability]
                    if add_prefix:
                        sub_ability = f"{cate}_{ability}_{sub_ability}"
                    sub_new_node = TreeNode(name=sub_ability, description=sub_ability_node.description, children={})
                    new_node.add_child(sub_new_node)

            subtree.add_child(new_node)

        return ToolSpec(
            name=f"{cate}_compose_tool",
            description=f"Tool with capabilities: {', '.join(capabilities)}.",
            category=cate,
            capabilities=selected,
            diversity=Diversity.MEDIUM,
            complexity=Complexity.MEDIUM,
            tree_node=subtree
        )

    async def _sample_high_complexity(self,
                                      category: str = None,
                                      min_category_count: int = 1,
                                      max_category_count: int = 3,
                                      min_abilities_count: int = 2,
                                      max_abilities_count: int = 4) -> ToolSpec:
        """Sample for high complexity, means cross-category capabilities."""
        categories = list(self.tool_tree.cate_nodes.keys())
        if not categories:
            return self._fallback_tool_spec('', Diversity.LOW, Complexity.LOW)

        # More strategies can be added...
        category_len = len(categories)
        selected_num = min(random.randint(min_category_count, max_category_count), category_len)
        if category_len <= min_category_count + 1:
            selected = categories
        else:
            if category:
                if category in categories:
                    selected = [category]
                    selected.extend(random.sample([cate for cate in categories if cate != category], selected_num - 1))
                else:
                    logger.info(f"special category {category} not in {categories}")
                    selected = random.sample(categories, selected_num)
            else:
                selected = random.sample(categories, selected_num)
        if category:
            category = selected[0]
        else:
            category = '__'.join(selected)
        subtree = TreeNode(name=f"cross_category_{category}", description="cross category compose tool", children={})
        capabilities = []

        for cate in selected:
            spec = await self._sample_medium_complexity(cate, min_abilities_count, max_abilities_count, add_prefix=True)
            capabilities.extend(spec.capabilities)
            subtree.add_child(spec.tree_node)

        return ToolSpec(
            name=f"cross_category_{category}_tool",
            description="",
            category=category,
            capabilities=capabilities,
            diversity=Diversity.HIGH,
            complexity=Complexity.HIGH,
            tree_node=subtree
        )

    async def _select_category(self, category: str = None) -> str:
        if category and category in self.tool_tree.cate_nodes:
            return category

        available_category = list(self.tool_tree.cate_nodes.keys())
        return random.choice(available_category) if available_category else None

    def _fallback_tool_spec(self, category: str, diversity: str, complexity: str) -> ToolSpec:
        return ToolSpec(
            name=f"{category}_fallback",
            description=f"{category}_fallback",
            category=category,
            capabilities=[f"{category} basic ability"],
            diversity=diversity,
            complexity=complexity,
            tree_node=TreeNode(category, f"{category} ability", {})
        )

    async def _tool_capabilities_unique(self, tool_spec: ToolSpec) -> ToolSpec:
        capabilities = tuple(sorted(tool_spec.capabilities))
        hash_val = hash(capabilities)
        while hash_val in self.capabilities_hash:
            abilities = [f"{ability}_rand{random.choices(ability)[0]}" for ability in tool_spec.capabilities]
            tool_spec.capabilities = abilities
            capabilities = tuple(sorted(tool_spec.capabilities))
            hash_val = hash(capabilities)

        self.capabilities_hash.add(hash_val)
        return tool_spec

    async def complexity(self, tool_spec: ToolSpec) -> float:
        """Calculate complexity score based on tool spec."""
        base_score = 0.3

        # Add complexity based on parameters
        params = tool_spec.parameters.get("properties", {})
        param_score = len(params) * 0.1

        # Add complexity based on nested structures
        nested_score = 0
        for param in params.values():
            if param.get("type") in ["object", "array"]:
                nested_score += 0.1

        return min(base_score + param_score + nested_score, 1.0)

    async def constraints(self, tool_spec: ToolSpec) -> List[str]:
        """Parse constraint list based on tool spec."""
        constraints = []
        params = tool_spec.parameters.get("properties", {})

        for param_name, param_def in params.items():
            if "minimum" in param_def:
                constraints.append(f"{param_name}_max: {param_def['minimum']}")
            if "maximum" in param_def:
                constraints.append(f"{param_name}_min: {param_def['maximum']}")
            if "pattern" in param_def:
                constraints.append(f"{param_name}_pattern: {param_def['pattern']}")

        return constraints

    async def diversity(self, tool_spec: ToolSpec) -> float:
        """Calculate diversity score based on category coverage and capability variety."""
        base_score = 0.2

        # Calculate diversity score based on the number of abilities
        capability_score = min(len(tool_spec.capabilities) * 0.1, 0.5)

        # Calculate diversity score based on the number of categories (if it is a cross category tool)
        category_score = 0.0
        if "__" in tool_spec.category:
            category_count = len(tool_spec.category.split("__"))
            category_score = min(category_count * 0.1, 0.3)

        return min(base_score + capability_score + category_score, 1.0)

    async def similarity(self, tool_a: ToolSpec, tool_b: ToolSpec) -> float:
        """Calculate similarity between two tools based on capabilities overlap."""
        if not tool_a.capabilities or not tool_b.capabilities:
            return 0.0

        set_a = set(tool_a.capabilities)
        set_b = set(tool_b.capabilities)

        intersection = len(set_a.intersection(set_b))
        union = len(set_a.union(set_b))

        return intersection / union if union > 0 else 0.0

    async def merge(self, tools: List[ToolSpec]) -> ToolSpec:
        """Merge multiple tools into one composite tool."""
        if not tools:
            raise ValueError("Cannot merge empty list of tools")

        if len(tools) == 1:
            return tools[0]

        # merge base info
        merged_name = "_".join([tool.name for tool in tools[:3]]) + "_merged"
        merged_description = "Merged tool combining: " + ", ".join([tool.description for tool in tools[:3]])

        # merge categories
        categories = list(set([tool.category for tool in tools if tool.category]))
        merged_category = "__".join(categories) if len(categories) > 1 else categories[0] if categories else ""

        # merge capabilities
        merged_capabilities = []
        for tool in tools:
            merged_capabilities.extend(tool.capabilities)
        merged_capabilities = list(set(merged_capabilities))

        # merge parameters
        merged_parameters = {}
        for tool in tools:
            merged_parameters.update(tool.parameters)

        # merge output parameters
        merged_output_parameters = {}
        for tool in tools:
            merged_output_parameters.update(tool.output_parameters)

        # merge dependencies
        merged_dependencies = []
        for tool in tools:
            merged_dependencies.extend(tool.dependencies)
        merged_dependencies = list(set(merged_dependencies))

        # merge constraints
        merged_constraints = []
        for tool in tools:
            merged_constraints.extend(tool.constraints)
        merged_constraints = list(set(merged_constraints))

        # Determine the complexity and diversity after the merge,
        # Take the highest complexity and diversity as the attributes of the merged tool
        complexities = [tool.complexity for tool in tools]
        diversities = [tool.diversity for tool in tools]

        merged_complexity = max(complexities,
                                key=lambda x: Complexity.types().index(x) if x in Complexity.types() else 0)
        merged_diversity = max(diversities, key=lambda x: Diversity.types().index(x) if x in Diversity.types() else 0)

        # create node after merge
        merged_tree_node = TreeNode(
            name=f"merged_{merged_category}",
            description="Merged tool node",
            children={}
        )

        for i, tool in enumerate(tools):
            if tool.tree_node:
                merged_tree_node.add_child(tool.tree_node)

        return ToolSpec(
            name=merged_name,
            description=merged_description,
            category=merged_category,
            tree_node=merged_tree_node,
            parameters=merged_parameters,
            output_parameters=merged_output_parameters,
            capabilities=merged_capabilities,
            dependencies=merged_dependencies,
            constraints=merged_constraints,
            diversity=merged_diversity,
            complexity=merged_complexity,
            metadata={"merged_from": [tool.name for tool in tools]}
        )

    async def filter_by_complexity(self, tools: List[ToolSpec], min_complexity: str = Complexity.LOW,
                                   max_complexity: str = Complexity.HIGH) -> List[ToolSpec]:
        """Filter tools by complexity range."""
        complexity_order = {Complexity.LOW: 0, Complexity.MEDIUM: 1, Complexity.HIGH: 2}

        min_level = complexity_order.get(min_complexity, 0)
        max_level = complexity_order.get(max_complexity, 2)

        filtered_tools = []
        for tool in tools:
            tool_level = complexity_order.get(tool.complexity, 1)
            if min_level <= tool_level <= max_level:
                filtered_tools.append(tool)

        return filtered_tools

    async def filter_by_diversity(self, tools: List[ToolSpec], min_diversity: str = Diversity.LOW,
                                  max_diversity: str = Diversity.HIGH) -> List[ToolSpec]:
        """Filter tools by diversity range."""
        diversity_order = {Diversity.LOW: 0, Diversity.MEDIUM: 1, Diversity.HIGH: 2}

        min_level = diversity_order.get(min_diversity, 0)
        max_level = diversity_order.get(max_diversity, 2)

        filtered_tools = []
        for tool in tools:
            tool_level = diversity_order.get(tool.diversity, 1)
            if min_level <= tool_level <= max_level:
                filtered_tools.append(tool)

        return filtered_tools

    async def categorize_tools(self, tools: List[ToolSpec]) -> Dict[str, List[ToolSpec]]:
        """Categorize tools by their category field."""
        categorized = {}
        for tool in tools:
            category = tool.category if tool.category else "uncategorized"
            if category not in categorized:
                categorized[category] = []
            categorized[category].append(tool)
        return categorized

    async def top_k_by_complexity(self, tools: List[ToolSpec], k: int = 5) -> List[ToolSpec]:
        """Get top K tools sorted by complexity."""
        complexity_order = {Complexity.LOW: 0, Complexity.MEDIUM: 1, Complexity.HIGH: 2}
        sorted_tools = sorted(tools, key=lambda t: complexity_order.get(t.complexity, 1), reverse=True)
        return sorted_tools[:k]

    async def top_k_by_diversity(self, tools: List[ToolSpec], k: int = 5) -> List[ToolSpec]:
        """Get top K tools sorted by diversity."""
        diversity_order = {Diversity.LOW: 0, Diversity.MEDIUM: 1, Diversity.HIGH: 2}
        sorted_tools = sorted(tools, key=lambda t: diversity_order.get(t.diversity, 1), reverse=True)
        return sorted_tools[:k]

    async def mutate(self, tool_spec: ToolSpec, mutation_rate: float = 0.3) -> ToolSpec:
        """Perform mutation operations on the tool."""
        # Create a copy of the tool
        mutated_tool = ToolSpec(
            name=tool_spec.name,
            description=tool_spec.description,
            category=tool_spec.category,
            tree_node=tool_spec.tree_node,
            parameters=tool_spec.parameters.copy() if tool_spec.parameters else {},
            output_parameters=tool_spec.output_parameters.copy() if tool_spec.output_parameters else {},
            capabilities=tool_spec.capabilities.copy(),
            dependencies=tool_spec.dependencies.copy() if tool_spec.dependencies else [],
            constraints=tool_spec.constraints.copy() if tool_spec.constraints else [],
            diversity=tool_spec.diversity,
            complexity=tool_spec.complexity,
            metadata=tool_spec.metadata.copy() if tool_spec.metadata else {}
        )

        # Perform different mutation operations based on the mutation rate

        if random.random() < mutation_rate:
            # increase random ability
            await self._mutate_add_random_capability(mutated_tool)

        if random.random() < mutation_rate:
            # modify complexity
            await self._mutate_complexity(mutated_tool)

        if random.random() < mutation_rate:
            # modify diversity
            await self._mutate_diversity(mutated_tool)

        if random.random() < mutation_rate * 0.5:
            # cross category combination
            await self._mutate_cross_category(mutated_tool)

        # ensure uniqueness of abilities
        await self._tool_capabilities_unique(mutated_tool)

        return mutated_tool

    async def _mutate_add_random_capability(self, tool_spec: ToolSpec):
        """Add random capability to the tool."""

        if tool_spec.category in self.tool_tree.cate_nodes:
            cate_node = self.tool_tree.cate_nodes[tool_spec.category]
            all_capabilities = list(cate_node.children.keys())

            # add 1-2 new abilities
            num_to_add = random.randint(1, 2)
            available_capabilities = [cap for cap in all_capabilities if cap not in tool_spec.capabilities]

            if available_capabilities:
                to_add = random.sample(available_capabilities, min(num_to_add, len(available_capabilities)))
                tool_spec.capabilities.extend(to_add)

                if tool_spec.description:
                    tool_spec.description += f", extended with {' and '.join(to_add)}"
                else:
                    tool_spec.description = f"Extended tool with {' and '.join(to_add)}"

    async def _mutate_complexity(self, tool_spec: ToolSpec):
        """Change the complexity of the tool."""
        complexity_levels = [Complexity.LOW, Complexity.MEDIUM, Complexity.HIGH]
        current_index = complexity_levels.index(tool_spec.complexity)

        change = random.choice([-1, 0, 1])
        new_index = max(0, min(len(complexity_levels) - 1, current_index + change))
        tool_spec.complexity = complexity_levels[new_index]

    async def _mutate_diversity(self, tool_spec: ToolSpec):
        """Change the diversity of tools."""
        diversity_levels = [Diversity.LOW, Diversity.MEDIUM, Diversity.HIGH]
        current_index = diversity_levels.index(tool_spec.diversity)

        change = random.choice([-1, 0, 1])
        new_index = max(0, min(len(diversity_levels) - 1, current_index + change))
        tool_spec.diversity = diversity_levels[new_index]

    async def _mutate_cross_category(self, tool_spec: ToolSpec):
        """Create cross category combination tools."""
        # not a cross category tool
        if "__" not in tool_spec.category:
            categories = list(self.tool_tree.cate_nodes.keys())
            if len(categories) > 1:
                # random select another category
                other_category = random.choice([cat for cat in categories if cat != tool_spec.category])

                if other_category in self.tool_tree.cate_nodes:
                    other_cate_node = self.tool_tree.cate_nodes[other_category]
                    other_capabilities = list(other_cate_node.children.keys())

                    # add 1-2 abilities from other categories
                    num_to_add = random.randint(1, 2)
                    to_add = random.sample(other_capabilities, min(num_to_add, len(other_capabilities)))
                    tool_spec.capabilities.extend([f"{other_category}_{cap}" for cap in to_add])

                    # update category to cross category
                    tool_spec.category = f"{tool_spec.category}__{other_category}"

                    # Improve complexity and diversity
                    tool_spec.complexity = Complexity.HIGH
                    tool_spec.diversity = Diversity.HIGH

    async def crossover(self, tool_a: ToolSpec, tool_b: ToolSpec, crossover_rate: float = 0.7) -> List[ToolSpec]:
        """Perform cross operation on two tools to generate a new tool."""
        if random.random() > crossover_rate:
            return [tool_a, tool_b]

        child1, child2 = await self._perform_crossover(tool_a, tool_b)
        return [child1, child2]

    async def _perform_crossover(self, tool_a: ToolSpec, tool_b: ToolSpec) -> tuple:
        """Perform specific cross operations."""

        # Inherit the basic information of tool_a and have mixed abilities
        child1 = ToolSpec(
            name=f"{tool_a.name}_{tool_b.name[:5]}",
            description=f"Hybrid of {tool_a.name} and {tool_b.name}",
            category=tool_a.category,
            parameters=tool_a.parameters.copy() if tool_a.parameters else {},
            output_parameters=tool_a.output_parameters.copy() if tool_a.output_parameters else {},
            capabilities=tool_a.capabilities.copy(),
            dependencies=list(set(tool_a.dependencies + tool_b.dependencies)),
            constraints=list(set(tool_a.constraints + tool_b.constraints)),
            diversity=max(tool_a.diversity, tool_b.diversity,
                          key=lambda x: Diversity.types().index(x) if x in Diversity.types() else 0),
            complexity=max(tool_a.complexity, tool_b.complexity,
                           key=lambda x: Complexity.types().index(x) if x in Complexity.types() else 0),
            metadata={"parents": [tool_a.name, tool_b.name]}
        )

        # Inherit the basic information of tool_b and have mixed abilities
        child2 = ToolSpec(
            name=f"{tool_b.name}_{tool_a.name[:5]}",
            description=f"Hybrid of {tool_b.name} and {tool_a.name}",
            category=tool_b.category,
            parameters=tool_b.parameters.copy() if tool_b.parameters else {},
            output_parameters=tool_b.output_parameters.copy() if tool_b.output_parameters else {},
            capabilities=tool_b.capabilities.copy(),
            dependencies=list(set(tool_a.dependencies + tool_b.dependencies)),
            constraints=list(set(tool_a.constraints + tool_b.constraints)),
            diversity=max(tool_a.diversity, tool_b.diversity,
                          key=lambda x: Diversity.types().index(x) if x in Diversity.types() else 0),
            complexity=max(tool_a.complexity, tool_b.complexity,
                           key=lambda x: Complexity.types().index(x) if x in Complexity.types() else 0),
            metadata={"parents": [tool_a.name, tool_b.name]}
        )

        # mixed abilities
        all_capabilities = list(set(tool_a.capabilities + tool_b.capabilities))
        if len(all_capabilities) > 2:
            # segmentation abilities
            split_point = random.randint(1, len(all_capabilities) - 1)
            child1.capabilities = all_capabilities[:split_point]
            child2.capabilities = all_capabilities[split_point:]
        else:
            child1.capabilities = all_capabilities[:1] if all_capabilities else tool_a.capabilities
            child2.capabilities = all_capabilities[1:] if len(all_capabilities) > 1 else tool_b.capabilities

        child1.tree_node = TreeNode(
            name=child1.category,
            description=f"Child of {tool_a.name} and {tool_b.name}",
            children={}
        )

        child2.tree_node = TreeNode(
            name=child2.category,
            description=f"Child of {tool_b.name} and {tool_a.name}",
            children={}
        )

        return child1, child2
