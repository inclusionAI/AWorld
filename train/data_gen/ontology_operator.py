# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import random
from copy import deepcopy
from typing import Dict, List

from aworld.logs.util import logger
from train.data_gen.schema import Diversity, Specification, Complexity, TreeNode
from train.data_gen.capability_ontology import CapabilityOntology


class OntologyOperator:
    """Operator calculation operation of specifications based on capability ontology structure."""

    def __init__(self, ontology: CapabilityOntology):
        # hierarchical structure is a tree
        self.ontology_tree = ontology
        self.capabilities_hash = set()

    async def samples(self,
                      category: str = None,
                      complexity_distribution: Dict[str, float] = None,
                      count: int = 5):
        """Sample a subtree from the context tree and create API specification

        Args:
            category: Ontology category, random value if None.
            complexity_distribution: Distribution of complexity sample.
            count: Sample category node of ontology tree count.

        Returns:
            List of sampled ontology spec
        """
        if not complexity_distribution:
            complexity_distribution = Diversity.default_distribute()

        specs = []
        for _ in range(count):
            complexity = await self._distribute_choice(complexity_distribution)
            spec = await self.sample(category=category, complexity=complexity)
            await self._capabilities_unique(spec)
            specs.append(spec)

        return specs

    async def sample(self, category: str = None, complexity: str = None) -> Specification:
        """Specification sampling based on complexity and category."""
        if not complexity:
            complexity = self._distribute_choice(Complexity.default_distribute())

        return await self.sample_by_complexity(category, complexity)

    async def sample_by_complexity(self,
                                   category: str = None,
                                   complexity: str = None,
                                   min_abilities_count: int = 2,
                                   max_abilities_count: int = 5,
                                   min_category_count: int = 1,
                                   max_category_count: int = 3) -> Specification:
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

    async def _sample_low_complexity(self, category: str = None) -> Specification:
        """Sample for low complexity, means specification with single capability of category."""
        cate = await self._select_category(category)
        if cate is None:
            return self._fallback_spec('', Diversity.LOW, Complexity.LOW)

        return await self.single_capability(cate)

    async def single_capability(self, cate: str, capability: str = None) -> Specification:
        """Build specification with single capability of category."""

        cate_node = self.ontology_tree.cate_nodes.get(cate)
        if cate_node is None:
            logger.warning(f"Category {cate} not found in ontology network")
            return self._fallback_spec('', Diversity.LOW, Complexity.LOW)
        if cate_node.children:
            ability = capability or random.choice(list(cate_node.children.keys()))
            ability_node = cate_node.children.get(ability)
            if ability_node is None:
                logger.warning(f"capability {ability} not found in ontology network")
                return self._fallback_spec(cate, Diversity.LOW, Complexity.LOW)

            subtree = TreeNode(name=cate, description=f"{cate} category", children={})
            node = TreeNode(name=ability,
                            description=ability_node.description,
                            children=deepcopy(ability_node.children))
            subtree.add_child(node)

            return Specification(
                name=f"{ability}_ability",
                description="",
                category=cate,
                capabilities=[ability],
                diversity=Diversity.LOW,
                complexity=Complexity.LOW,
                tree_node=subtree
            )

        return self._fallback_spec(cate, Diversity.LOW, Complexity.LOW)

    async def _sample_medium_complexity(self,
                                        category: str = None,
                                        min_abilities_count: int = 2,
                                        max_abilities_count: int = 4,
                                        add_prefix: bool = False) -> Specification:
        """Sample for medium complexity, means specification with multiple (config num) related capabilities and sub capabilities."""
        cate = await self._select_category(category)
        if cate is None:
            return self._fallback_spec('', Diversity.LOW, Complexity.LOW)
        cate_node = self.ontology_tree.cate_nodes[cate]

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

        return Specification(
            name=f"{cate}_compose",
            description=f"Specification with capabilities: {', '.join(capabilities)}.",
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
                                      max_abilities_count: int = 4) -> Specification:
        """Sample for high complexity, means cross-category capabilities."""
        categories = list(self.ontology_tree.cate_nodes.keys())
        if not categories:
            return self._fallback_spec('', Diversity.LOW, Complexity.LOW)

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
        subtree = TreeNode(name=f"cross_category_{category}", description="cross category compose", children={})
        capabilities = []

        for cate in selected:
            spec = await self._sample_medium_complexity(cate, min_abilities_count, max_abilities_count, add_prefix=True)
            capabilities.extend(spec.capabilities)
            subtree.add_child(spec.tree_node)

        return Specification(
            name=f"cross_category_{category}",
            description="",
            category=category,
            capabilities=capabilities,
            diversity=Diversity.HIGH,
            complexity=Complexity.HIGH,
            tree_node=subtree
        )

    async def _select_category(self, category: str = None) -> str:
        if category and category in self.ontology_tree.cate_nodes:
            return category

        available_category = list(self.ontology_tree.cate_nodes.keys())
        return random.choice(available_category) if available_category else None

    def _fallback_spec(self, category: str, diversity: str, complexity: str) -> Specification:
        if not category and self.ontology_tree.cate_nodes:
            category = random.choice(list(self.ontology_tree.cate_nodes.keys()))

        return Specification(
            name=f"{category}_fallback",
            description=f"{category}_fallback",
            category=category,
            capabilities=[f"{category} basic ability"],
            diversity=diversity,
            complexity=complexity,
            tree_node=TreeNode(category, f"{category} ability", {})
        )

    async def _capabilities_unique(self, spec: Specification) -> Specification:
        capabilities = tuple(sorted(spec.capabilities))
        hash_val = hash(capabilities)
        while hash_val in self.capabilities_hash:
            abilities = [f"{ability}_rand{random.choices(ability)[0]}" for ability in spec.capabilities]
            spec.capabilities = abilities
            capabilities = tuple(sorted(spec.capabilities))
            hash_val = hash(capabilities)

        self.capabilities_hash.add(hash_val)
        return spec

    async def deduplication(self, specs: List[Specification]) -> List[Specification]:
        """Remove duplicates from the list of generated specs."""
        capabilities = set()
        unique_specs = []

        for spec in specs:
            cap_tuple = tuple(sorted(spec.capabilities))
            if cap_tuple not in capabilities:
                capabilities.add(cap_tuple)
                unique_specs.append(spec)

        return unique_specs

    async def complexity(self, spec: Specification) -> float:
        """Calculate complexity score based on spec."""
        base_score = 0.3

        # Add complexity based on parameters
        params = spec.parameters.get("properties", {})
        param_score = len(params) * 0.1

        # Add complexity based on nested structures
        nested_score = 0
        for param in params.values():
            if param.get("type") in ["object", "array"]:
                nested_score += 0.1

        return min(base_score + param_score + nested_score, 1.0)

    async def constraints(self, spec: Specification) -> List[str]:
        """Parse constraint list based on spec."""
        constraints = []
        params = spec.parameters.get("properties", {})

        for param_name, param_def in params.items():
            if "minimum" in param_def:
                constraints.append(f"{param_name}_max: {param_def['minimum']}")
            if "maximum" in param_def:
                constraints.append(f"{param_name}_min: {param_def['maximum']}")
            if "pattern" in param_def:
                constraints.append(f"{param_name}_pattern: {param_def['pattern']}")

        return constraints

    async def diversity(self, spec: Specification) -> float:
        """Calculate diversity score based on category coverage and capability variety."""
        base_score = 0.2

        # Calculate diversity score based on the number of abilities
        capability_score = min(len(spec.capabilities) * 0.1, 0.5)

        # Calculate diversity score based on the number of categories (if it is a cross category)
        category_score = 0.0
        if "__" in spec.category:
            category_count = len(spec.category.split("__"))
            category_score = min(category_count * 0.1, 0.3)

        return min(base_score + capability_score + category_score, 1.0)

    async def similarity(self, spec_a: Specification, spec_b: Specification) -> float:
        """Calculate similarity between two specs based on capabilities overlap."""
        if not spec_a.capabilities or not spec_b.capabilities:
            return 0.0

        set_a = set(spec_a.capabilities)
        set_b = set(spec_b.capabilities)

        intersection = len(set_a.intersection(set_b))
        union = len(set_a.union(set_b))

        return intersection / union if union > 0 else 0.0

    async def merge(self, specs: List[Specification]) -> Specification:
        """Merge multiple specs into one composite spec."""
        if not specs:
            raise ValueError("Cannot merge empty list of specs")

        if len(specs) == 1:
            return specs[0]

        # merge base info
        merged_name = "_".join([spec.name for spec in specs[:3]]) + "_merged"
        merged_description = "Merged ability combining: " + ", ".join([spec.description for spec in specs[:3]])

        # merge categories
        categories = list(set([spec.category for spec in specs if spec.category]))
        merged_category = "__".join(categories) if len(categories) > 1 else categories[0] if categories else ""

        # merge capabilities
        merged_capabilities = []
        for spec in specs:
            merged_capabilities.extend(spec.capabilities)
        merged_capabilities = list(set(merged_capabilities))

        # merge parameters
        merged_parameters = {}
        for spec in specs:
            merged_parameters.update(spec.parameters)

        # merge output parameters
        merged_output_parameters = {}
        for spec in specs:
            merged_output_parameters.update(spec.output_parameters)

        # merge dependencies
        merged_dependencies = []
        for spec in specs:
            merged_dependencies.extend(spec.dependencies)
        merged_dependencies = list(set(merged_dependencies))

        # merge constraints
        merged_constraints = []
        for spec in specs:
            merged_constraints.extend(spec.constraints)
        merged_constraints = list(set(merged_constraints))

        # Determine the complexity and diversity after the merge,
        # Take the highest complexity and diversity as the attributes of the merged spec
        complexities = [spec.complexity for spec in specs]
        diversities = [spec.diversity for spec in specs]

        merged_complexity = max(complexities,
                                key=lambda x: Complexity.types().index(x) if x in Complexity.types() else 0)
        merged_diversity = max(diversities, key=lambda x: Diversity.types().index(x) if x in Diversity.types() else 0)

        # create node after merge
        merged_tree_node = TreeNode(
            name=f"merged_{merged_category}",
            description="Merged spec node",
            children={}
        )

        for i, spec in enumerate(specs):
            if spec.tree_node:
                merged_tree_node.add_child(spec.tree_node)

        return Specification(
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
            metadata={"merged_from": [spec.name for spec in specs]}
        )

    async def filter_by_complexity(self, specs: List[Specification], min_complexity: str = Complexity.LOW,
                                   max_complexity: str = Complexity.HIGH) -> List[Specification]:
        """Filter specs by complexity range."""
        complexity_order = {Complexity.LOW: 0, Complexity.MEDIUM: 1, Complexity.HIGH: 2}

        min_level = complexity_order.get(min_complexity, 0)
        max_level = complexity_order.get(max_complexity, 2)

        filtered_specs = []
        for spec in specs:
            _level = complexity_order.get(spec.complexity, 1)
            if min_level <= _level <= max_level:
                filtered_specs.append(spec)

        return filtered_specs

    async def filter_by_diversity(self, specs: List[Specification], min_diversity: str = Diversity.LOW,
                                  max_diversity: str = Diversity.HIGH) -> List[Specification]:
        """Filter specs by diversity range."""
        diversity_order = {Diversity.LOW: 0, Diversity.MEDIUM: 1, Diversity.HIGH: 2}

        min_level = diversity_order.get(min_diversity, 0)
        max_level = diversity_order.get(max_diversity, 2)

        filtered_specs = []
        for spec in specs:
            _level = diversity_order.get(spec.diversity, 1)
            if min_level <= _level <= max_level:
                filtered_specs.append(spec)

        return filtered_specs

    async def categorize_specs(self, specs: List[Specification]) -> Dict[str, List[Specification]]:
        """Categorize specs by their category field."""
        categorized = {}
        for spec in specs:
            category = spec.category if spec.category else "uncategorized"
            if category not in categorized:
                categorized[category] = []
            categorized[category].append(spec)
        return categorized

    async def top_k_by_complexity(self, specs: List[Specification], k: int = 5) -> List[Specification]:
        """Get top K specs sorted by complexity."""
        complexity_order = {Complexity.LOW: 0, Complexity.MEDIUM: 1, Complexity.HIGH: 2}
        sorted_specs = sorted(specs, key=lambda t: complexity_order.get(t.complexity, 1), reverse=True)
        return sorted_specs[:k]

    async def top_k_by_diversity(self, specs: List[Specification], k: int = 5) -> List[Specification]:
        """Get top K specs sorted by diversity."""
        diversity_order = {Diversity.LOW: 0, Diversity.MEDIUM: 1, Diversity.HIGH: 2}
        sorted_specs = sorted(specs, key=lambda t: diversity_order.get(t.diversity, 1), reverse=True)
        return sorted_specs[:k]

    async def mutate(self, spec: Specification, mutation_rate: float = 0.3) -> Specification:
        """Perform mutation operations on the spec."""
        # Create a copy of the spec
        mutated_spec = Specification(
            name=spec.name,
            description=spec.description,
            category=spec.category,
            tree_node=spec.tree_node,
            parameters=spec.parameters.copy() if spec.parameters else {},
            output_parameters=spec.output_parameters.copy() if spec.output_parameters else {},
            capabilities=spec.capabilities.copy(),
            dependencies=spec.dependencies.copy() if spec.dependencies else [],
            constraints=spec.constraints.copy() if spec.constraints else [],
            diversity=spec.diversity,
            complexity=spec.complexity,
            metadata=spec.metadata.copy() if spec.metadata else {}
        )

        # Perform different mutation operations based on the mutation rate

        if random.random() < mutation_rate:
            # increase random ability
            await self._mutate_add_random_capability(mutated_spec)

        if random.random() < mutation_rate:
            # modify complexity
            await self._mutate_complexity(mutated_spec)

        if random.random() < mutation_rate:
            # modify diversity
            await self._mutate_diversity(mutated_spec)

        if random.random() < mutation_rate * 0.5:
            # cross category combination
            await self._mutate_cross_category(mutated_spec)

        # ensure uniqueness of abilities
        await self._capabilities_unique(mutated_spec)

        return mutated_spec

    async def _mutate_add_random_capability(self, spec: Specification):
        """Add random capability to the spec."""

        if spec.category in self.ontology_tree.cate_nodes:
            cate_node = self.ontology_tree.cate_nodes[spec.category]
            all_capabilities = list(cate_node.children.keys())

            # add 1-2 new abilities
            num_to_add = random.randint(1, 2)
            available_capabilities = [cap for cap in all_capabilities if cap not in spec.capabilities]

            if available_capabilities:
                to_add = random.sample(available_capabilities, min(num_to_add, len(available_capabilities)))
                spec.capabilities.extend(to_add)

                if spec.description:
                    spec.description += f", extended with {' and '.join(to_add)}"
                else:
                    spec.description = f"Extended spec with {' and '.join(to_add)}"

    async def _mutate_complexity(self, spec: Specification):
        """Change the complexity of the spec."""
        complexity_levels = [Complexity.LOW, Complexity.MEDIUM, Complexity.HIGH]
        current_index = complexity_levels.index(spec.complexity)

        change = random.choice([-1, 0, 1])
        new_index = max(0, min(len(complexity_levels) - 1, current_index + change))
        spec.complexity = complexity_levels[new_index]

    async def _mutate_diversity(self, spec: Specification):
        """Change the diversity of specs."""
        diversity_levels = [Diversity.LOW, Diversity.MEDIUM, Diversity.HIGH]
        current_index = diversity_levels.index(spec.diversity)

        change = random.choice([-1, 0, 1])
        new_index = max(0, min(len(diversity_levels) - 1, current_index + change))
        spec.diversity = diversity_levels[new_index]

    async def _mutate_cross_category(self, spec: Specification):
        """Create cross category combination specs."""
        # not a cross category spec
        if "__" not in spec.category:
            categories = list(self.ontology_tree.cate_nodes.keys())
            if len(categories) > 1:
                # random select another category
                other_category = random.choice([cat for cat in categories if cat != spec.category])

                if other_category in self.ontology_tree.cate_nodes:
                    other_cate_node = self.ontology_tree.cate_nodes[other_category]
                    other_capabilities = list(other_cate_node.children.keys())

                    # add 1-2 abilities from other categories
                    num_to_add = random.randint(1, 2)
                    to_add = random.sample(other_capabilities, min(num_to_add, len(other_capabilities)))
                    spec.capabilities.extend([f"{other_category}_{cap}" for cap in to_add])

                    # update category to cross category
                    spec.category = f"{spec.category}__{other_category}"

                    # Improve complexity and diversity
                    spec.complexity = Complexity.HIGH
                    spec.diversity = Diversity.HIGH

    async def crossover(self, spec_a: Specification, spec_b: Specification, crossover_rate: float = 0.7) -> List[
        Specification]:
        """Perform cross operation on two specs to generate a new spec."""
        if random.random() > crossover_rate:
            return [spec_a, spec_b]

        child1, child2 = await self._perform_crossover(spec_a, spec_b)
        return [child1, child2]

    async def _perform_crossover(self, spec_a: Specification, spec_b: Specification) -> tuple:
        """Perform specific cross operations."""

        # Inherit the basic information of spec_a and have mixed abilities
        child1 = Specification(
            name=f"{spec_a.name}_{spec_b.name[:5]}",
            description=f"Hybrid of {spec_a.name} and {spec_b.name}",
            category=spec_a.category,
            parameters=spec_a.parameters.copy() if spec_a.parameters else {},
            output_parameters=spec_a.output_parameters.copy() if spec_a.output_parameters else {},
            capabilities=spec_a.capabilities.copy(),
            dependencies=list(set(spec_a.dependencies + spec_b.dependencies)),
            constraints=list(set(spec_a.constraints + spec_b.constraints)),
            diversity=max(spec_a.diversity, spec_b.diversity,
                          key=lambda x: Diversity.types().index(x) if x in Diversity.types() else 0),
            complexity=max(spec_a.complexity, spec_b.complexity,
                           key=lambda x: Complexity.types().index(x) if x in Complexity.types() else 0),
            metadata={"parents": [spec_a.name, spec_b.name]}
        )

        # Inherit the basic information of spec_b and have mixed abilities
        child2 = Specification(
            name=f"{spec_b.name}_{spec_a.name[:5]}",
            description=f"Hybrid of {spec_b.name} and {spec_a.name}",
            category=spec_b.category,
            parameters=spec_b.parameters.copy() if spec_b.parameters else {},
            output_parameters=spec_b.output_parameters.copy() if spec_b.output_parameters else {},
            capabilities=spec_b.capabilities.copy(),
            dependencies=list(set(spec_a.dependencies + spec_b.dependencies)),
            constraints=list(set(spec_a.constraints + spec_b.constraints)),
            diversity=max(spec_a.diversity, spec_b.diversity,
                          key=lambda x: Diversity.types().index(x) if x in Diversity.types() else 0),
            complexity=max(spec_a.complexity, spec_b.complexity,
                           key=lambda x: Complexity.types().index(x) if x in Complexity.types() else 0),
            metadata={"parents": [spec_a.name, spec_b.name]}
        )

        # mixed abilities
        all_capabilities = list(set(spec_a.capabilities + spec_b.capabilities))
        if len(all_capabilities) > 2:
            # segmentation abilities
            split_point = random.randint(1, len(all_capabilities) - 1)
            child1.capabilities = all_capabilities[:split_point]
            child2.capabilities = all_capabilities[split_point:]
        else:
            child1.capabilities = all_capabilities[:1] if all_capabilities else spec_a.capabilities
            child2.capabilities = all_capabilities[1:] if len(all_capabilities) > 1 else spec_b.capabilities

        child1.tree_node = TreeNode(
            name=child1.category,
            description=f"Child of {spec_a.name} and {spec_b.name}",
            children={}
        )

        child2.tree_node = TreeNode(
            name=child2.category,
            description=f"Child of {spec_b.name} and {spec_a.name}",
            children={}
        )

        return child1, child2
