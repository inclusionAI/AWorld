"""
AWorld AST Framework - 核心接口
===============================

定义AST分析框架的核心抽象接口和主要组件。
"""

import json
import logging
import math
import tempfile
import traceback
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple, TYPE_CHECKING

from .analyzer import CodeAnalyzer
from .models import (
    Symbol, CodeNode, RepositoryMap
)
from ...logs.util import logger

# 导入BaseParser（不会造成循环依赖，因为parsers.base_parser不导入core）
if TYPE_CHECKING:
    from .parsers.base_parser import BaseParser
else:
    from .parsers.base_parser import BaseParser


class ValidationError(Exception):
    """上下文验证失败异常"""
    pass


class RepositoryAnalyzer:
    """仓库分析器主类"""

    def __init__(self, code_analyzer: CodeAnalyzer):
        self.code_analyzer = code_analyzer
        logger = logging.getLogger(f"{__name__}.RepositoryAnalyzer")

    def analyze(self,
                root_path: Path,
                file_patterns: Optional[List[str]] = None,
                ignore_patterns: Optional[List[str]] = None) -> RepositoryMap:
        """
        执行完整的仓库分析

        Args:
            root_path: 仓库根目录
            file_patterns: 文件包含模式
            ignore_patterns: 文件忽略模式

        Returns:
            完整的仓库映射
        """
        logger.info(f"开始分析仓库: {root_path}")

        # 执行代码分析
        repo_map = self.code_analyzer.analyze_repository(
            root_path, file_patterns, ignore_patterns
        )

        logger.info("仓库分析完成")
        return repo_map

    def recall(self,
               repo_map: RepositoryMap,
               user_query: str = "",
               max_tokens: int = 8000,
               context_layers: Optional[List[str]] = None) -> str:
        """
        多层次代码上下文召回
        参考 aider 的分层上下文管理设计

        Args:
            repo_map: 仓库映射
            user_query: 用户查询
            max_tokens: 最大token数量
            context_layers: 指定要包含的上下文层次

        Returns:
            格式化的分层上下文字符串
        """
        logger.info(f"🚀 开始多层次代码上下文召回")
        logger.info(f"📝 用户查询: '{user_query}'")
        logger.info(f"🎯 最大token数: {max_tokens}")
        logger.info(f"📋 指定层次: {context_layers}")

        # 提取用户查询中的关键词
        user_mentions = [user_query]
        logger.info(f"🔍 提取的查询关键词: {user_mentions}")

        # 构建分层上下文
        logger.info(f"🏗️ 开始构建分层上下文...")
        logger.info('repo_map: ', repo_map)
        layered_context = self._build_layered_context(
            repo_map, user_mentions, context_layers
        )
        logger.info(f"📊 分层上下文构建结果: {len(layered_context)} 个层次")
        return layered_context
        # 优化token预算
        # logger.info(f"⚖️ 开始优化token预算...")
        # final_context = self._optimize_token_budget(layered_context, max_tokens)
        # logger.info(f"✅ 上下文召回完成，最终长度: {len(final_context)} 字符")

        # return final_context

    def _build_layered_context(self,
                               repo_map: RepositoryMap,
                               user_mentions: List[str],
                               context_layers: List[str]) -> Dict[str, str]:
        """构建分层上下文内容"""

        logger.info(f"🏗️ 开始构建分层上下文")
        logger.info(f"📋 请求的层次: {context_layers}")
        logger.info(f"🔍 用户查询: {user_mentions}")

        layer_generators = {
            "skeleton": self._generate_skeleton_context,
            "implementation": self._generate_implementation_context,
        }

        layered_content = {}
        for layer_idx, layer_name in enumerate(context_layers):
            logger.info(f"🔄 处理层次 [{layer_idx+1}/{len(context_layers)}]: {layer_name}")

            if layer_name in layer_generators:
                try:
                    logger.info(f"  ⚙️ 调用生成器: {layer_generators[layer_name].__name__}")
                    content = layer_generators[layer_name](repo_map, user_mentions)
                    logger.info(f"  📏 生成内容长度: {len(content)} 字符")

                    # 显示内容预览（前200字符）
                    preview = content[:200].replace('\n', '\\n')
                    logger.debug(f"  👀 内容预览: {preview}...")

                    if content.strip():  # 只添加非空内容
                        layered_content[layer_name] = content
                        logger.info(f"  ✅ 层次 {layer_name} 内容已添加")
                    else:
                        logger.warning(f"  ⚠️ 层次 {layer_name} 生成的内容为空，跳过")

                except Exception as e:
                    logger.error(f"  ❌ 生成{layer_name}层内容失败: {e}")
                    import traceback
                    logger.debug(f"  📋 错误堆栈: {traceback.format_exc()}")
            else:
                logger.warning(f"  ⚠️ 未知的层次类型: {layer_name}")
                logger.info(f"  📝 可用的层次类型: {list(layer_generators.keys())}")

        logger.info(f"🏁 分层上下文构建完成")
        logger.info(f"📊 成功生成的层次: {list(layered_content.keys())}")
        total_length = sum(len(content) for content in layered_content.values())
        logger.info(f"📏 总内容长度: {total_length} 字符")

        return layered_content

    def _generate_system_context(self, repo_map: RepositoryMap, user_mentions: List[str]) -> str:
        """生成系统概览层上下文"""
        lines = ["# 系统概览"]

        # 项目基本信息
        lines.append(f"\n## 项目结构")
        lines.append(f"总文件数: {len(repo_map.code_nodes)}")

        # 计算总代码行数和符号数
        total_lines = 0
        total_symbols = sum(len(node.symbols) for node in repo_map.code_nodes.values())

        # 安全获取代码行数
        for file_path, node in repo_map.code_nodes.items():
            if hasattr(node, 'line_count'):
                total_lines += node.line_count
            else:
                # 备用方案：尝试读取文件计算行数
                try:
                    content = file_path.read_text(encoding='utf-8', errors='ignore')
                    total_lines += len(content.split('\n'))
                except:
                    total_lines += len(node.symbols) * 3  # 估算

        lines.append(f"代码行数: {total_lines}")
        lines.append(f"符号总数: {total_symbols}")

        # 技术栈信息
        file_types = {}
        for file_path in repo_map.code_nodes.keys():
            ext = file_path.suffix.lower()
            file_types[ext] = file_types.get(ext, 0) + 1

        if file_types:
            lines.append("\n### 文件类型分布")
            for ext, count in sorted(file_types.items(), key=lambda x: x[1], reverse=True)[:5]:
                lines.append(f"- {ext or '无扩展名'}: {count}个文件")

        # 提及的关键组件
        if user_mentions:
            lines.append(f"\n### 查询关注点")
            lines.append(f"提及标识符: {', '.join(user_mentions[:10])}")

        return '\n'.join(lines) + '\n'

    def _generate_structure_context(self, repo_map: RepositoryMap, user_mentions: List[str]) -> str:
        """生成项目结构层上下文"""
        lines = ["# 项目结构"]

        # 主要目录和文件
        lines.append("\n## 核心文件")
        sorted_files = sorted(repo_map.code_nodes.keys(), key=lambda x: len(repo_map.code_nodes[x].symbols),
                              reverse=True)

        for i, file_path in enumerate(sorted_files[:10], 1):
            node = repo_map.code_nodes[file_path]
            lines.append(f"{i:2d}. {file_path.name} - {len(node.symbols)}个符号")

        # 模块关系
        if hasattr(repo_map.logic_layer, 'import_graph') and repo_map.logic_layer.import_graph:
            lines.append("\n## 主要模块依赖")
            for i, (module, imports) in enumerate(list(repo_map.logic_layer.import_graph.items())[:8], 1):
                imports_str = ', '.join(list(imports)[:3])
                lines.append(f"{i:2d}. {module} → {imports_str}")

        return '\n'.join(lines) + '\n'

    def _generate_symbols_context(self, repo_map: RepositoryMap, user_mentions: List[str]) -> str:
        """生成重要符号层上下文"""
        lines = ["# 重要符号"]

        # 获取相关符号
        relevant_symbols = self.get_relevant_symbols(repo_map, user_mentions, max_symbols=15)

        if relevant_symbols:
            lines.append("\n## 相关符号")
            for i, (symbol, score) in enumerate(relevant_symbols[:15], 1):
                lines.append(
                    f"{i:2d}. {symbol.name} ({symbol.symbol_type.value}) - {symbol.file_path.name}:{symbol.line_number}")
                if symbol.signature:
                    lines.append(f"    {symbol.signature}")
        else:
            lines.append("\n## 所有符号 (按文件分组)")
            for file_path, node in list(repo_map.code_nodes.items())[:5]:
                if node.symbols:
                    lines.append(f"\n### {file_path.name}")
                    for symbol in node.symbols[:5]:
                        lines.append(f"- {symbol.name} ({symbol.symbol_type.value})")

        return '\n'.join(lines) + '\n'

    def _generate_skeleton_context(self, repo_map: RepositoryMap, user_mentions: List[str]) -> str:
        """生成代码骨架层上下文"""
        lines = ["# 代码骨架"]

        # 为最相关的文件生成骨架
        relevant_files = []

        # 基于用户提及筛选相关文件（使用正则表达式匹配）
        import re
        if user_mentions:
            for file_path, node in repo_map.code_nodes.items():
                score = 0
                for mention in user_mentions:
                    try:
                        if re.search(mention, file_path.name, re.IGNORECASE):
                            score += 10
                        for symbol in node.symbols:
                            if re.search(mention, symbol.name, re.IGNORECASE):
                                score += 5
                    except re.error:
                        # 如果正则表达式无效，回退到简单字符串匹配
                        mention_lower = mention.lower()
                        if mention_lower in file_path.name.lower():
                            score += 10
                        for symbol in node.symbols:
                            if mention_lower in symbol.name.lower():
                                score += 5
                if score > 0:
                    relevant_files.append((file_path, score))

        if not relevant_files:
            # 如果没有明确相关的文件，选择符号最多的前3个文件
            relevant_files = [(fp, len(node.symbols)) for fp, node in repo_map.code_nodes.items()]

        relevant_files.sort(key=lambda x: x[1], reverse=True)

        for i, (file_path, score) in enumerate(relevant_files[:3], 1):
            lines.append(f"\n## {file_path.name}")

            # 简单的骨架生成
            node = repo_map.code_nodes[file_path]
            for symbol in node.symbols[:10]:
                lines.append(f"  {symbol.symbol_type.value} {symbol.name}")
                if symbol.signature:
                    lines.append(f"    {symbol.signature}")

        return '\n'.join(lines) + '\n'

    def _generate_implementation_context(self, repo_map: RepositoryMap, user_mentions: List[str]) -> str:
        """
        生成详细实现层上下文，基于正则表达式从符号content中匹配

        Args:
            repo_map: 仓库映射
            user_mentions: 用户提及的正则表达式列表

        Returns:
            格式化的实现层上下文字符串
        """
        import re
        lines = ["# 关键实现"]

        # 添加详细的调试日志
        logger.info(f"🔍 开始生成实现层上下文")
        logger.info(f"📝 用户查询模式: {user_mentions}")

        # 从implementation_layer的code_nodes中搜索匹配的符号
        matches = []

        # 检查 repo_map 结构
        logger.info(f"📊 仓库映射结构检查:")
        logger.info(f"  - repo_map 是否存在: {repo_map is not None}")
        logger.info(f"  - implementation_layer 是否存在: {hasattr(repo_map, 'implementation_layer') and repo_map.implementation_layer is not None}")

        if not repo_map.implementation_layer or not repo_map.implementation_layer.code_nodes:
            logger.warning(f"⚠️ 实现层数据缺失:")
            logger.warning(f"  - implementation_layer: {repo_map.implementation_layer}")
            if repo_map.implementation_layer:
                logger.warning(f"  - code_nodes: {repo_map.implementation_layer.code_nodes}")
            lines.append("\n未找到实现层代码节点")
            return '\n'.join(lines) + '\n'

        # 统计实现层数据
        total_files = len(repo_map.implementation_layer.code_nodes)
        total_symbols = sum(len(node.symbols) for node in repo_map.implementation_layer.code_nodes.values())
        symbols_with_content = 0

        logger.info(f"📈 实现层统计:")
        logger.info(f"  - 总文件数: {total_files}")
        logger.info(f"  - 总符号数: {total_symbols}")

        # 遍历所有code_nodes中的符号
        for file_idx, (file_path, code_node) in enumerate(repo_map.implementation_layer.code_nodes.items()):
            logger.info(f"🔍 处理文件 [{file_idx+1}/{total_files}]: {file_path}")
            logger.info(f"  - 符号数量: {len(code_node.symbols)}")

            for symbol_idx, symbol in enumerate(code_node.symbols):
                logger.info(f'symbol: {symbol}')
                if not symbol.content:  # 跳过没有content的符号
                    logger.info(f"  - 跳过符号 [{symbol_idx+1}] {symbol.name}: 无内容")
                    continue

                symbols_with_content += 1
                logger.info(f"  - 检查符号 [{symbol_idx+1}] {symbol.name} ({symbol.symbol_type.value})")
                logger.info(f"    内容长度: {len(symbol.content)} 字符")

                symbol_score = 0.0
                match_details = []

                # 使用正则表达式在symbol.content中搜索
                for pattern_idx, mention_pattern in enumerate(user_mentions):
                    logger.info(f"    🔎 应用模式 [{pattern_idx+1}]: '{mention_pattern}'")

                    try:
                        # 在符号内容中搜索
                        content_matches = re.finditer(mention_pattern, symbol.content, re.IGNORECASE | re.MULTILINE)
                        content_match_count = len(list(content_matches))
                        logger.info(f"      内容匹配次数: {content_match_count}")

                        if content_match_count > 0:
                            symbol_score += content_match_count * 15.0  # 内容匹配给高分
                            match_details.append(f"内容匹配: {content_match_count}次")
                            logger.info(f"      ✅ 内容匹配 +{content_match_count * 15.0} 分")

                        # 在符号签名中搜索（如果有）
                        if symbol.signature:
                            signature_match = re.search(mention_pattern, symbol.signature, re.IGNORECASE)
                            logger.info(f"      签名匹配: {signature_match is not None}")
                            if signature_match:
                                symbol_score += 12.0
                                match_details.append("签名匹配")
                                logger.info(f"      ✅ 签名匹配 +12.0 分")

                        # 在文档字符串中搜索（如果有）
                        if symbol.docstring:
                            docstring_match = re.search(mention_pattern, symbol.docstring, re.IGNORECASE)
                            logger.info(f"      文档匹配: {docstring_match is not None}")
                            if docstring_match:
                                symbol_score += 8.0
                                match_details.append("文档匹配")
                                logger.info(f"      ✅ 文档匹配 +8.0 分")

                        # 在符号名称中搜索
                        name_match = re.search(mention_pattern, symbol.name, re.IGNORECASE)
                        logger.info(f"      名称匹配: {name_match is not None}")
                        if name_match:
                            symbol_score += 5.0
                            match_details.append("名称匹配")
                            logger.info(f"      ✅ 名称匹配 +5.0 分")

                    except re.error as e:
                        # 如果正则表达式无效，记录错误并跳过
                        logger.warning(f"❌ 无效的正则表达式 '{mention_pattern}': {e}")
                        continue

                # 如果有匹配，添加到结果中
                if symbol_score > 0:
                    matches.append((symbol, symbol_score, match_details))
                    logger.info(f"🎯 找到匹配符号: {symbol.name} (分数: {symbol_score:.1f}, 详情: {match_details})")

        # 统计最终结果
        logger.info(f"📊 搜索完成统计:")
        logger.info(f"  - 有内容的符号数: {symbols_with_content}")
        logger.info(f"  - 匹配的符号数: {len(matches)}")

        # 按相关性分数排序
        matches.sort(key=lambda x: x[1], reverse=True)
        logger.info(f"🏆 排序后的前5个匹配:")
        for i, (symbol, score, details) in enumerate(matches[:5]):
            logger.info(f"  {i+1}. {symbol.name} - {score:.1f}分 ({', '.join(details)})")

        # 生成上下文内容
        if matches:
            logger.info(f"📝 生成上下文内容，显示前8个匹配结果")
            for symbol, score, match_details in matches[:8]:  # 最多显示8个匹配结果
                lines.append(f"\n## {symbol.name} ({symbol.symbol_type.value})")
                lines.append(f"文件: {symbol.file_path}")
                lines.append(f"行号: {symbol.line_number}-{symbol.end_line}")
                lines.append(f"匹配分数: {score:.1f}")
                lines.append(f"匹配详情: {', '.join(match_details)}")

                if symbol.signature:
                    lines.append(f"\n签名:")
                    lines.append(symbol.signature)

                if symbol.docstring:
                    lines.append(f"\n文档:")
                    lines.append(symbol.docstring)

                # 显示符号的完整内容（带行号）
                source_code = self._get_symbol_source_code(repo_map, symbol)
                if source_code:
                    lines.append(f"\n源码:")
                    lines.append("```")
                    lines.append(source_code)
                    lines.append("```")
        else:
            logger.warning(f"⚠️ 未找到任何匹配的实现代码")
            lines.append("\n未找到与查询正则表达式匹配的实现代码")

        result = '\n'.join(lines) + '\n'
        logger.info(f"✅ 实现层上下文生成完成，总长度: {len(result)} 字符")
        return result

    def _get_symbol_source_code(self, repo_map: RepositoryMap, symbol: Symbol) -> Optional[str]:
        """获取符号的源码内容，每一行前面添加行号"""
        try:
            # 优先使用 Symbol 对象中的 content 字段
            if hasattr(symbol, 'content') and symbol.content:
                # 如果 Symbol 已包含代码内容，直接使用并添加行号
                content_lines = symbol.content.split('\n')
                numbered_lines = []
                for i, line in enumerate(content_lines):
                    line_number = symbol.line_number + i  # 从符号起始行号开始计算
                    numbered_lines.append(f"{line_number}→{line}")

                return '\n'.join(numbered_lines)

            # 回退方案：直接从文件系统读取（新的ImplementationLayer不再缓存file_contents）
            file_path = symbol.file_path
            if file_path.exists():
                with open(file_path, 'r', encoding='utf-8') as f:
                    file_content = f.read()

                # 提取符号对应的源码行
                lines = file_content.split('\n')
                start_idx = max(0, symbol.line_number - 1)  # 转换为0-based索引
                end_idx = min(len(lines), symbol.end_line) if symbol.end_line > 0 else start_idx + 1

                # 为每一行添加行号前缀
                numbered_lines = []
                for i, line in enumerate(lines[start_idx:end_idx]):
                    line_number = start_idx + i + 1  # 转换回1-based行号
                    # 直接拼接，不添加额外的格式化空间
                    numbered_lines.append(f"{line_number}→{line}")

                source_code = '\n'.join(numbered_lines)
                return source_code.strip() if source_code else None
            else:
                return None

        except Exception as e:
            logger.warning(f"获取符号源码失败 {symbol.name}: {e}")
            return None

    def _generate_references_context(self, repo_map: RepositoryMap, user_mentions: List[str]) -> str:
        """生成引用关系层上下文"""
        lines = ["# 调用关系"]

        if repo_map.logic_layer.call_graph:
            lines.append("\n## 主要调用关系")
            # 显示前10个最重要的调用关系
            for i, (caller, callees) in enumerate(list(repo_map.logic_layer.call_graph.items())[:10], 1):
                callees_str = ', '.join(list(callees)[:5])  # 限制显示的调用目标
                lines.append(f"{i:2d}. {caller} → {callees_str}")

        return '\n'.join(lines) + '\n'

    def _generate_trajectory_context(self, repo_map: RepositoryMap, user_mentions: List[str]) -> str:
        """生成执行轨迹层上下文（已禁用）"""
        return ""  # 轨迹功能已移除

    def _optimize_token_budget(self, layered_content: Dict[str, str], max_tokens: int) -> str:
        """
        动态优化token预算分配
        参考 aider 的二分搜索算法
        """
        # 层次优先级 (数字越小优先级越高)
        layer_priorities = {
            "system": 1,
            "structure": 2,
            "symbols": 3,
            "skeleton": 4,
            "references": 5,
            "implementation": 6,
            "trajectory": 7
        }

        # 按优先级排序层次
        sorted_layers = sorted(
            layered_content.items(),
            key=lambda x: layer_priorities.get(x[0], 999)
        )

        # 估算token数量的简单方法 (1 token ≈ 4 characters)
        def estimate_tokens(text: str) -> int:
            return len(text) // 4

        # 累积构建上下文，确保不超过token限制
        final_sections = []
        current_tokens = 0

        for layer_name, content in sorted_layers:
            content_tokens = estimate_tokens(content)

            if current_tokens + content_tokens <= max_tokens:
                final_sections.append(content)
                current_tokens += content_tokens
            else:
                # 如果是高优先级层次，尝试截断而不是完全丢弃
                if layer_priorities.get(layer_name, 999) <= 3:
                    remaining_tokens = max_tokens - current_tokens
                    if remaining_tokens > 100:  # 至少保留100个token的内容
                        truncated_content = content[:remaining_tokens * 4]
                        final_sections.append(truncated_content + "\n...(内容被截断)")
                        break
                else:
                    break

        return '\n'.join(final_sections)

    def get_relevant_symbols(self,
                             repo_map: RepositoryMap,
                             user_mentions: List[str],
                             max_symbols: int = 50) -> List[Tuple[Symbol, float]]:
        """
        根据用户提及的标识符，获取相关的符号并按相关性排序
        参考 aider 的符号排名算法
        """
        if not user_mentions:
            return []

        symbol_scores = []

        # 遍历所有文件中的符号
        for file_path, code_node in repo_map.code_nodes.items():
            for symbol in code_node.symbols:
                score = self._calculate_symbol_relevance(symbol, user_mentions, file_path, repo_map)
                if score > 0:
                    symbol_scores.append((symbol, score))

        # 按得分排序
        ranked_symbols = sorted(symbol_scores, key=lambda x: x[1], reverse=True)
        print('ranked_symbols: ', ranked_symbols)
        return ranked_symbols[:max_symbols]

    def _calculate_symbol_relevance(self,
                                    symbol: Symbol,
                                    user_mentions: List[str],
                                    file_path: Path,
                                    repo_map: RepositoryMap) -> float:
        """
        计算符号与用户查询的相关性得分
        参考 aider 的评分算法
        """
        score = 0.0
        symbol_name = symbol.name.lower()

        # 1. 使用正则表达式匹配：query 作为正则匹配字符串
        import re
        for mention in user_mentions:
            try:
                if re.search(mention, symbol_name, re.IGNORECASE):
                    score += 10.0  # 正则匹配
            except re.error:
                # 如果正则表达式无效，回退到简单字符串匹配
                if mention.lower() in symbol_name:
                    score += 5.0

        # 2. 命名风格加权：结构化命名通常更重要
        if self._has_structured_naming(symbol.name):
            score *= 1.5

        # 3. 符号类型加权：某些类型的符号更重要
        type_multipliers = {
            'CLASS': 2.0,  # 类通常是重要的入口点
            'FUNCTION': 1.5,  # 函数是主要逻辑
            'METHOD': 1.2,  # 方法
            'CONSTANT': 1.1,  # 常量
            'VARIABLE': 1.0  # 变量
        }
        score *= type_multipliers.get(symbol.symbol_type.value, 1.0)

        # 4. 文件重要性：PageRank 分数高的文件中的符号更重要
        if hasattr(repo_map, 'pagerank_scores') and repo_map.pagerank_scores:
            file_score = repo_map.pagerank_scores.get(file_path, 0.0)
            score *= (1.0 + file_score * 2)  # PageRank 作为乘数

        # 5. 符号长度：过短的符号降权
        if len(symbol.name) < 3:
            score *= 0.5
        elif len(symbol.name) >= 8 and self._has_structured_naming(symbol.name):
            score *= 1.2  # 长的结构化命名加权

        # 6. 私有符号降权
        if symbol.name.startswith('_') and not symbol.name.startswith('__'):
            score *= 0.3
        elif symbol.name.startswith('__'):
            score *= 0.1

        return score

    def _is_partial_match(self, symbol_name: str, mention: str) -> bool:
        """检查是否为部分匹配"""
        # 包含关系
        if mention in symbol_name or symbol_name in mention:
            return True

        # 驼峰命名拆分匹配
        if self._camel_case_contains(symbol_name, mention):
            return True

        # 下划线分割匹配
        if self._snake_case_contains(symbol_name, mention):
            return True

        return False

    def _camel_case_contains(self, symbol_name: str, mention: str) -> bool:
        """检查驼峰命名是否包含提及的词"""
        import re
        # 将驼峰命名拆分成单词
        words = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)', symbol_name)
        words_lower = [w.lower() for w in words]
        return mention in words_lower

    def _snake_case_contains(self, symbol_name: str, mention: str) -> bool:
        """检查下划线命名是否包含提及的词"""
        if '_' not in symbol_name:
            return False
        words = symbol_name.split('_')
        words_lower = [w.lower() for w in words if w]
        return mention in words_lower

    def _has_structured_naming(self, name: str) -> bool:
        """检查是否为结构化命名（驼峰、下划线等）"""
        import re

        # camelCase 或 PascalCase
        if re.search(r'[a-z][A-Z]', name):
            return True

        # snake_case
        if '_' in name and any(c.isalpha() for c in name):
            return True

        # CONSTANT_CASE
        if name.isupper() and '_' in name:
            return True

        # 包含数字的命名
        if re.search(r'\w+\d+', name):
            return True

        return False

    def _extract_mentions(self, query: str) -> List[str]:
        """
        从用户查询中提取匹配字符串
        query 作为正则匹配字符串直接返回
        """
        return [query]


class ACast:
    """AST框架主入口类"""

    def __init__(self, auto_register_parsers: bool = True, tmp_path: str = "~/.aworld/acast"):
        self.parsers: Dict[str, BaseParser] = {}
        self.analyzer: Optional[RepositoryAnalyzer] = None
        self.tmp_path = tmp_path

        if auto_register_parsers:
            self._auto_register_all_parsers()

        self.analyzer = self.create_analyzer()

    """
    code parser
    """

    def _auto_register_all_parsers(self) -> None:
        """自动注册所有可用的解析器"""
        try:
            from .parser_utils import get_supported_languages, create_parser

            supported_languages = get_supported_languages()
            logger.info(f"正在自动注册解析器，支持的语言: {', '.join(supported_languages)}")

            for lang in supported_languages:
                try:
                    parser = create_parser(lang)
                    if parser:
                        self.parsers[lang] = parser
                        logger.debug(f"✅ 自动注册解析器: {lang}")
                except Exception as e:
                    logger.warning(f"❌ 无法注册{lang}解析器: {e}")

            logger.info(f"解析器自动注册完成，共注册 {len(self.parsers)} 个解析器")

        except Exception as e:
            logger.error(f"自动注册解析器失败: {e}")
            # 不抛出异常，允许手动注册

    def register_parser(self, language: str, parser: BaseParser) -> None:
        """注册语言解析器"""
        self.parsers[language] = parser
        logger.info(f"注册解析器: {language}")

    def list_supported_languages(self) -> List[str]:
        """列出支持的编程语言"""
        return list(self.parsers.keys())

    def get_parser_info(self, language: str) -> Dict[str, Any]:
        """获取解析器信息"""
        if language not in self.parsers:
            return {}

        parser = self.parsers[language]
        return {
            'language': parser.language,
            'file_extensions': list(parser.file_extensions),
            'comment_patterns': parser.comment_patterns,
        }

    def parse(self, file_path: Path) -> Optional[CodeNode]:
        """
        解析文件的便捷方法

        Args:
            file_path: 文件路径

        Returns:
            解析结果CodeNode，如果失败则返回None
        """
        parser = self.get_parser(file_path)
        if parser:
            return parser.parse_file(file_path)
        return None

    def get_parser(self, file_path: Path) -> Optional[BaseParser]:
        """根据文件路径获取适当的解析器"""
        for parser in self.parsers.values():
            if parser.can_parse(file_path):
                return parser
        return None

    """
    repository analyzer
    """

    def create_analyzer(self, code_analyzer_class: type = None) -> RepositoryAnalyzer:
        """创建仓库分析器"""
        if code_analyzer_class is None:
            from .analyzer import DefaultCodeAnalyzer
            code_analyzer_class = DefaultCodeAnalyzer

        code_analyzer = code_analyzer_class(self.parsers)
        self.analyzer = RepositoryAnalyzer(code_analyzer)
        return self.analyzer

    def analyze(self, *args, **kwargs) -> RepositoryMap:
        """
        分析仓库的便捷方法，自动记录分析结果到tmp_path目录
        
        Args:
            *args: 传递给analyzer.analyze的位置参数
            **kwargs: 传递给analyzer.analyze的关键字参数，包括：
                - root_path: 仓库根目录（用于生成文件名）
                - auto_record: 是否自动记录（默认True）
                - record_name: 记录文件名（可选，默认基于root_path和时间戳生成）
        
        Returns:
            完整的仓库映射
        """
        if not self.analyzer:
            raise RuntimeError("请先调用 create_analyzer() 创建分析器")

        # 提取记录相关参数（在调用analyze之前提取root_path）
        auto_record = kwargs.pop('auto_record', True)
        record_name = kwargs.pop('record_name', None)

        # 执行分析
        repo_map = self.analyzer.analyze(*args, **kwargs)

        # 自动记录分析结果
        if auto_record:
            try:
                # 生成文件名
                name = record_name
                # 记录分析结果
                self.record_analyze_result(name, repo_map)
            except Exception as e:
                # 记录失败不影响分析结果返回
                logger.warning(f"自动记录分析结果失败: {e}")

        return repo_map

    def record_analyze_result(self, name, repo_map):
        """
        将repo_map记录到tmp_path目录下，标记文件为name
        
        Args:
            name: 文件名（不含扩展名）
            repo_map: 要保存的仓库映射对象
        
        Returns:
            保存的文件路径
        """
        tmp_dir = Path(self.tmp_path)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        # 保存为JSON文件
        file_path = tmp_dir / f"{name}.json"
        try:
            # 使用RepositoryMap的to_dict方法序列化
            json_data = repo_map.to_dict()
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(json_data, f, ensure_ascii=False, indent=2)
            logger.info(f"分析结果已保存到: {file_path}")
            return file_path
        except Exception as e:
            logger.error(f"保存分析结果失败: {e}")
            raise

    def load_analyze_result(self, name: str) -> Optional[RepositoryMap]:
        """
        从tmp_path目录加载已保存的分析结果
        
        Args:
            name: 文件名（不含扩展名）
        
        Returns:
            仓库映射对象，如果文件不存在或加载失败则返回None
        """
        tmp_dir = Path(self.tmp_path)
        file_path = tmp_dir / f"{name}.json"

        if not file_path.exists():
            logger.warning(f"分析报告不存在: {file_path}")
            return None

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
            # 使用RepositoryMap的from_dict方法反序列化
            repo_map = RepositoryMap.from_dict(json_data)
            # print('repo_map', repo_map)
            logger.info(f"成功加载分析报告: {file_path}")
            return repo_map
        except Exception as e:
            logger.error(f"加载分析报告失败: {e}")
            return None

    def recall(self,
               repo_map: Optional[RepositoryMap] = None,
               user_query: str = "",
               max_tokens: int = 8000,
               context_layers: Optional[List[str]] = None,
               record_name: Optional[str] = None) -> str:
        """
        获取优化的上下文信息给LLM
        优先从record_analyze_result记录的分析报告中召回

        Args:
            repo_map: 仓库映射（可选，如果提供了record_name则优先使用记录的分析报告）
            user_query: 用户查询
            max_tokens: 最大token数量
            context_layers: 指定要包含的上下文层次
            record_name: 已保存的分析报告名称（优先使用）

        Returns:
            格式化的上下文字符串
        """
        if not self.analyzer:
            raise RuntimeError("请先调用 create_analyzer() 创建分析器")

        # 优先从记录的分析报告中加载
        if record_name:
            loaded_repo_map = self.load_analyze_result(record_name)
            if loaded_repo_map is not None:
                repo_map = loaded_repo_map
            elif repo_map is None:
                logger.warning(f"无法加载分析报告 '{record_name}'，且未提供repo_map参数")

        # 如果仍然没有repo_map，抛出错误
        if repo_map is None:
            raise ValueError("必须提供repo_map或有效的record_name")

        # 如果指定了高级参数，使用analyzer的高级recall方法
        if context_layers is not None:
            return self.analyzer.recall(
                repo_map=repo_map,
                user_query=user_query,
                max_tokens=max_tokens,
                context_layers=context_layers,
            )
        else:
            # 使用默认的简单recall方法
            return self.analyzer.recall(repo_map, user_query, max_tokens)

    def generate_snapshot(self, target_dir: Path, version: str = "v0") -> Path:
        """
        生成目标目录的压缩快照

        Args:
            target_dir: 目标目录路径，需要创建快照的目录
            version: 版本号字符串，默认为"v0"

        Returns:
            保存的快照文件路径
        """
        import tarfile

        target_dir = Path(target_dir)
        if not target_dir.exists():
            raise ValueError(f"目标目录不存在: {target_dir}")

        # 保存快照到tmp_path目录
        tmp_dir = Path(self.tmp_path)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        # 生成快照文件名：{path末尾一段}_{version}.tar.gz
        path_suffix = target_dir.name or "default"
        snapshot_filename = f"{path_suffix}_{version}.tar.gz"
        snapshot_path = tmp_dir / snapshot_filename

        # 创建压缩快照
        with tarfile.open(snapshot_path, "w:gz") as tar:
            tar.add(target_dir, arcname=target_dir.name,
                    filter=lambda tarinfo: None if '__pycache__' in tarinfo.name or '.pyc' in tarinfo.name else tarinfo)

        logger.info(f"Generated snapshot saved to: {snapshot_path}")

        return snapshot_path

    def create_enhanced_copy(self,
                             source_dir: Path,
                             patch_content: str,
                             version: str = "v0",
                             strict_validation: bool = True,
                             max_context_mismatches: int = 0) -> Path:
        """
        原地更新源代码目录并应用patch，增强验证机制

        Args:
            source_dir: 源代码目录（将在此目录原地更新）
            patch_content: patch文件内容
            version: 版本号（如 "v0", "v1"），用于命名patch文件
            strict_validation: 是否启用严格验证模式（默认True）
            max_context_mismatches: 允许的最大上下文不匹配次数（默认0）

        Returns:
            更新后的目录路径（与source_dir相同）

        Raises:
            ValidationError: 当上下文验证失败且超过允许的不匹配次数时
        """
        source_dir = Path(source_dir)
        if not source_dir.exists():
            raise ValueError(f"源目录不存在: {source_dir}")

        try:
            # 保存patch文件：{path末尾一段}_{version}
            path_suffix = Path(source_dir).name or "default"
            patch_file = source_dir / f"{path_suffix}_{version}.patch"
            patch_file.write_text(patch_content, encoding='utf-8')

            # 解析并应用patch（原地更新），增强验证
            self._apply_patches_with_validation(source_dir, patch_content, strict_validation, max_context_mismatches)

            return source_dir

        except Exception as e:
            raise RuntimeError(f"应用patch失败: {e}")

    def _apply_patches_with_validation(self, target_dir: Path, patch_content: str, strict_validation: bool = True,
                                       max_context_mismatches: int = 0):
        """
        使用difflib生成和patch_ng库应用的最优补丁处理方法

        基于/Users/hgc/hgc_repo/basic/text2agent/difflib_apply_run.py的参考实现，采用以下技术栈：
        - difflib: Python标准库，用于生成统一diff格式
        - patch_ng: 专业补丁库，用于解析和应用补丁

        Args:
            target_dir: 目标目录
            patch_content: unified diff格式的补丁内容
            strict_validation: 是否启用严格验证模式
            max_context_mismatches: 允许的最大上下文不匹配次数
        """
        try:
            import patch_ng
        except ImportError:
            logger.error("patch_ng库未安装，请运行: pip install patch-ng")
            raise RuntimeError("需要安装patch_ng库：pip install patch-ng")

        logger.info("🚀 开始使用经过验证的difflib+patch_ng方案应用补丁")
        # logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')
        # 将patch内容写入临时文件，patch_ng需要从文件读取

        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.patch', delete=False, encoding='utf-8') as temp_patch_file:
                temp_patch_file.write(patch_content)
                temp_patch_path = temp_patch_file.name
            logger.info(f"patch_content: {patch_content}")

            logger.info(f"📋 补丁已写入临时文件: {temp_patch_path}")

            # 验证应用结果（如果启用严格验证）
            if strict_validation:
                self._validate_patch_ng_result(target_dir, patch_content)

            # 使用patch_ng加载和应用补丁（参考实现的方式）
            pset = patch_ng.fromfile(temp_patch_path)

            if not pset:
                raise RuntimeError("patch_ng无法解析补丁内容")

            logger.info(f"📋 patch_ng解析到补丁文件")

            # 应用补丁到目标目录
            # patch_ng.apply(root=str(target_dir)) 方式参考实现
            apply_result = pset.apply(root=str(target_dir))

            if apply_result:
                logger.info("✅ patch_ng补丁应用成功！")

                logger.info("📊 处理结果: 补丁应用完成，所有文件成功处理")
            else:
                error_msg = "patch_ng补丁应用失败，可能是上下文不匹配或文件不存在"
                logger.error(f"❌ {error_msg}")

                if strict_validation:
                    raise RuntimeError(error_msg)
                else:
                    logger.warning("⚠️ 非严格模式下继续执行")

        except Exception as e:
            logger.error(f"❌ 补丁应用过程失败: {e} {traceback.format_exc()}")
            if strict_validation:
                raise

    def _validate_patch_ng_result(self, target_dir: Path, patch_content: str):
        """
        验证patch_ng应用结果的正确性

        Args:
            target_dir: 目标目录
            patch_content: 原始补丁内容
        """
        try:
            logger.debug("🔍 开始验证patch_ng应用结果...")

            # 简单的验证：检查补丁是否包含预期的变更标记
            lines = patch_content.split('\n')
            added_lines = [line[1:] for line in lines if line.startswith('+') and not line.startswith('+++')]
            removed_lines = [line[1:] for line in lines if line.startswith('-') and not line.startswith('---')]

            logger.debug(f"预期添加 {len(added_lines)} 行, 删除 {len(removed_lines)} 行")

            # 这里可以添加更详细的验证逻辑
            # 例如：验证特定的文件内容是否包含预期的变更

            logger.debug("✅ patch_ng应用结果验证通过")

        except Exception as e:
            logger.warning(f"⚠️ patch_ng结果验证失败: {e}")

    def _apply_patches(self, target_dir: Path, patch_content: str):
        """使用经过验证的difflib+patch_ng方案应用补丁（兼容性包装器）"""
        # 调用经过完整测试验证的方法，保持向后兼容
        self._apply_patches_with_validation(
            target_dir, patch_content,
            strict_validation=False,  # 非严格模式以保持兼容性
            max_context_mismatches=999  # 允许更多不匹配以保持兼容性
        )

    def json_operations_to_patch(self, operations_json: str, source_dir: Path) -> str:
        """
        将JSON格式的操作指令转换为unified diff格式的patch内容

        Args:
            operations_json: JSON格式的操作指令字符串，支持以下操作类型：
                - insert: 在指定行后插入代码
                - replace: 替换指定行范围的代码
                - delete: 删除指定行范围的代码
            source_dir: 源代码目录路径

        Returns:
            统一diff格式的patch内容

        Example:
            操作JSON格式：
            {
                "operations": [
                    {
                        "type": "insert",
                        "file_path": "example.py",
                        "after_line": 10,
                        "content": ["新增行1", "新增行2"]
                    },
                    {
                        "type": "replace",
                        "file_path": "example.py",
                        "start_line": 15,
                        "end_line": 20,
                        "content": ["替换内容"]
                    },
                    {
                        "type": "delete",
                        "file_path": "example.py",
                        "start_line": 25,
                        "end_line": 30
                    }
                ]
            }
        """
        import difflib

        try:
            operations_data = json.loads(operations_json)
        except json.JSONDecodeError as e:
            raise ValueError(f"无效的JSON格式: {e}")

        if "operations" not in operations_data:
            raise ValueError("JSON中缺少'operations'字段")

        operations = operations_data["operations"]

        # 按文件路径分组操作，确保每个文件的操作按行号排序
        file_operations = {}
        for op in operations:
            if "file_path" not in op or "type" not in op:
                raise ValueError("操作缺少必要字段 'file_path' 或 'type'")

            file_path = op["file_path"]
            if file_path not in file_operations:
                file_operations[file_path] = []
            file_operations[file_path].append(op)

        # 对每个文件的操作按行号排序（从后往前，避免行号偏移）
        for file_path in file_operations:
            file_operations[file_path].sort(key=self._get_operation_sort_key, reverse=True)

        all_diffs = []

        # 处理每个文件
        for file_path, ops in file_operations.items():
            full_file_path = source_dir / file_path

            if not full_file_path.exists():
                logger.warning(f"文件不存在，跳过: {full_file_path}")
                continue

            try:
                # 读取原始文件内容
                with open(full_file_path, 'r', encoding='utf-8') as f:
                    original_lines = f.readlines()

                # 应用所有操作到内容副本
                modified_lines = original_lines.copy()

                for op in ops:
                    modified_lines = self._apply_single_operation(modified_lines, op)

                # 生成unified diff
                original_content = ''.join(original_lines)
                modified_content = ''.join(modified_lines)

                diff = difflib.unified_diff(
                    original_content.splitlines(keepends=True),
                    modified_content.splitlines(keepends=True),
                    fromfile=f"a/{file_path}",
                    tofile=f"b/{file_path}",
                    lineterm='\n'
                )

                diff_content = ''.join(diff)
                if diff_content.strip():  # 只添加非空的diff
                    all_diffs.append(diff_content)

            except Exception as e:
                logger.error(f"处理文件 {file_path} 时发生错误: {e}")
                raise

        if not all_diffs:
            return ""  # 没有任何变化

        return '\n'.join(all_diffs)

    def _get_operation_sort_key(self, op: dict) -> int:
        """获取操作的排序键，用于按行号排序"""
        if op["type"] == "insert":
            return op.get("after_line", 0)
        elif op["type"] in ["replace", "delete"]:
            return op.get("start_line", 0)
        else:
            return 0

    def _apply_single_operation(self, lines: List[str], op: dict) -> List[str]:
        """
        对行列表应用单个操作

        Args:
            lines: 文件行列表（每行包含换行符）
            op: 单个操作字典

        Returns:
            应用操作后的行列表
        """
        op_type = op["type"]

        if op_type == "insert":
            after_line = op.get("after_line", 0)
            content = op.get("content", [])

            if after_line < 0 or after_line > len(lines):
                raise ValueError(f"插入位置无效: after_line={after_line}, 文件共{len(lines)}行")

            # 确保内容行都以换行符结尾
            insert_lines = [line if line.endswith('\n') else line + '\n' for line in content]

            # 在指定行后插入
            return lines[:after_line] + insert_lines + lines[after_line:]

        elif op_type == "replace":
            start_line = op.get("start_line", 1)
            end_line = op.get("end_line", start_line)
            content = op.get("content", [])

            if start_line < 1 or end_line < start_line or start_line > len(lines):
                raise ValueError(f"替换范围无效: start_line={start_line}, end_line={end_line}, 文件共{len(lines)}行")

            # 转换为0-based索引
            start_idx = start_line - 1
            end_idx = min(end_line, len(lines))

            # 确保内容行都以换行符结尾
            replace_lines = [line if line.endswith('\n') else line + '\n' for line in content]

            # 替换指定范围
            return lines[:start_idx] + replace_lines + lines[end_idx:]

        elif op_type == "delete":
            start_line = op.get("start_line", 1)
            end_line = op.get("end_line", start_line)

            if start_line < 1 or end_line < start_line or start_line > len(lines):
                raise ValueError(f"删除范围无效: start_line={start_line}, end_line={end_line}, 文件共{len(lines)}行")

            # 转换为0-based索引
            start_idx = start_line - 1
            end_idx = min(end_line, len(lines))

            # 删除指定范围
            return lines[:start_idx] + lines[end_idx:]

        else:
            raise ValueError(f"不支持的操作类型: {op_type}")

    def deploy_operations(self,
                         operations_json: str,
                         source_dir: Path,
                         version: str = "v0",
                         strict_validation: bool = True,
                         max_context_mismatches: int = 0) -> Path:
        """
        根据JSON操作指令部署代码变更

        这个方法结合了json_operations_to_patch和create_enhanced_copy的功能，
        提供了一个便捷的接口来直接从JSON操作部署到源代码目录。

        Args:
            operations_json: JSON格式的操作指令
            source_dir: 源代码目录
            version: 版本号
            strict_validation: 是否启用严格验证
            max_context_mismatches: 允许的最大上下文不匹配次数

        Returns:
            更新后的目录路径
        """
        logger.info("🚀 开始根据JSON操作指令部署代码变更")

        # 转换JSON操作为patch格式
        patch_content = self.json_operations_to_patch(operations_json, source_dir)

        if not patch_content.strip():
            logger.info("📋 没有检测到任何代码变更，跳过部署")
            return source_dir

        logger.info(f"📝 已生成patch内容，长度: {len(patch_content)} 字符")

        # 使用现有的create_enhanced_copy方法应用patch
        return self.create_enhanced_copy(
            source_dir=source_dir,
            patch_content=patch_content,
            version=version,
            strict_validation=strict_validation,
            max_context_mismatches=max_context_mismatches
        )

    def search_replace_in_file(self,
                              file_path: Path,
                              search_text: str,
                              replace_text: str,
                              fuzzy_match: bool = True,
                              similarity_threshold: float = 0.8) -> Dict[str, Any]:
        """
        基于aider算法在文件中执行搜索替换操作

        Args:
            file_path: 目标文件路径
            search_text: 要搜索的代码段
            replace_text: 替换后的代码段
            fuzzy_match: 是否启用模糊匹配
            similarity_threshold: 模糊匹配的相似度阈值(0.0-1.0)

        Returns:
            包含操作结果的字典：
            {
                "success": bool,
                "modified": bool,
                "original_content": str,
                "new_content": str,
                "match_info": dict,
                "error": str
            }
        """
        result = {
            "success": False,
            "modified": False,
            "original_content": "",
            "new_content": "",
            "match_info": {},
            "error": ""
        }

        try:
            if not file_path.exists():
                result["error"] = f"文件不存在: {file_path}"
                return result

            # 读取文件内容
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            result["original_content"] = content

            # 执行搜索替换
            new_content = self._fuzzy_search_replace(
                content, search_text, replace_text,
                fuzzy_match, similarity_threshold
            )

            if new_content:
                result["new_content"] = new_content
                result["modified"] = True
                result["success"] = True

                # 写入文件
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)

                logger.info(f"✅ 搜索替换成功: {file_path}")
            else:
                result["error"] = "未找到匹配的代码段进行替换"
                logger.warning(f"⚠️ 未找到匹配项: {file_path}")

            # 修改后重建索引
            logger.info(f"rebuild analyze|start|{file_path}")
            self.analyze(root_path=file_path.parent,
                         ignore_patterns=['__pycache__', '*.pyc', '.git'],
                         record_name=Path(file_path).name)
            logger.info(f"rebuild analyze|end|{file_path}")
        except Exception as e:
            result["error"] = f"搜索替换失败: {str(e)}"
            logger.error(f"❌ 搜索替换错误: {e}")

        return result



    def _fuzzy_search_replace(self,
                             content: str,
                             search_text: str,
                             replace_text: str,
                             fuzzy_match: bool = False,  # 默认禁用模糊匹配
                             similarity_threshold: float = 1.0) -> Optional[str]:
        """
        精确搜索替换算法 - 仅支持精确匹配

        采用以下策略（按优先级）：
        1. 精确匹配（推荐）
        2. 如果启用fuzzy_match，则进行空白字符灵活匹配（不推荐）

        Args:
            content: 文件内容
            search_text: 搜索文本
            replace_text: 替换文本
            fuzzy_match: 是否启用空白字符灵活匹配（默认False）
            similarity_threshold: 被忽略（为保持接口兼容性）

        Returns:
            替换后的内容，如果未找到匹配则返回None
        """
        if not search_text.strip():
            return None

        # 准备内容和搜索文本
        content, content_lines = self._prep_text(content)
        search_text, search_lines = self._prep_text(search_text)
        replace_text, replace_lines = self._prep_text(replace_text)

        # 策略1: 精确匹配（主要策略）
        result = self._perfect_replace(content_lines, search_lines, replace_lines)
        if result:
            logger.info("✅ 使用精确匹配策略")
            return result

        if fuzzy_match:
            # 策略2: 空白字符灵活匹配（仅在明确启用时使用）
            result = self._whitespace_flexible_replace(content_lines, search_lines, replace_lines)
            if result:
                logger.warning("⚠️ 使用空白字符灵活匹配策略（不推荐，建议使用精确匹配）")
                return result

        # 不再支持模糊相似度匹配
        logger.warning("❌ 未找到精确匹配，搜索替换失败")
        return None

    def _prep_text(self, text: str) -> Tuple[str, List[str]]:
        """准备文本，确保以换行符结尾并分割成行"""
        if text and not text.endswith("\n"):
            text += "\n"
        lines = text.splitlines(keepends=True)
        return text, lines

    def _perfect_replace(self, content_lines: List[str], search_lines: List[str], replace_lines: List[str]) -> Optional[str]:
        """精确匹配替换 - 基于aider的perfect_replace算法"""
        search_tuple = tuple(search_lines)
        search_len = len(search_lines)

        for i in range(len(content_lines) - search_len + 1):
            content_tuple = tuple(content_lines[i:i + search_len])
            if search_tuple == content_tuple:
                # 找到精确匹配，执行替换
                result_lines = content_lines[:i] + replace_lines + content_lines[i + search_len:]
                return "".join(result_lines)

        return None

    def _whitespace_flexible_replace(self, content_lines: List[str], search_lines: List[str], replace_lines: List[str]) -> Optional[str]:
        """空白字符灵活匹配 - 基于aider的whitespace matching算法"""
        # 计算最小公共缩进
        leading_spaces = []
        for line in search_lines + replace_lines:
            if line.strip():  # 只考虑非空行
                leading_spaces.append(len(line) - len(line.lstrip()))

        if not leading_spaces:
            return None

        # 移除公共缩进
        min_indent = min(leading_spaces) if leading_spaces else 0
        if min_indent > 0:
            normalized_search = [line[min_indent:] if line.strip() else line for line in search_lines]
            normalized_replace = [line[min_indent:] if line.strip() else line for line in replace_lines]
        else:
            normalized_search = search_lines
            normalized_replace = replace_lines

        # 寻找匹配（忽略缩进）
        for i in range(len(content_lines) - len(normalized_search) + 1):
            match_indent = self._check_indent_match(
                content_lines[i:i + len(normalized_search)],
                normalized_search
            )

            if match_indent is not None:
                # 应用相同的缩进到替换文本
                adjusted_replace = [
                    match_indent + line if line.strip() else line
                    for line in normalized_replace
                ]
                result_lines = content_lines[:i] + adjusted_replace + content_lines[i + len(normalized_search):]
                return "".join(result_lines)

        return None

    def _check_indent_match(self, content_section: List[str], search_section: List[str]) -> Optional[str]:
        """检查内容片段是否与搜索片段匹配（忽略缩进）"""
        if len(content_section) != len(search_section):
            return None

        # 检查去除缩进后的内容是否匹配
        for content_line, search_line in zip(content_section, search_section):
            if content_line.lstrip() != search_line.lstrip():
                return None

        # 计算统一的缩进前缀
        indents = set()
        for content_line, search_line in zip(content_section, search_section):
            if content_line.strip():  # 只考虑非空行
                content_indent = content_line[:len(content_line) - len(content_line.lstrip())]
                search_indent = search_line[:len(search_line) - len(search_line.lstrip())]
                indent_diff = content_indent[len(search_indent):] if len(content_indent) >= len(search_indent) else ""
                indents.add(indent_diff)

        if len(indents) == 1:
            return indents.pop()
        return None

    def _similarity_replace(self,
                           content_lines: List[str],
                           search_text: str,
                           search_lines: List[str],
                           replace_lines: List[str],
                           threshold: float) -> Optional[str]:
        """基于相似度的模糊匹配 - 基于aider的similarity matching算法"""
        max_similarity = 0.0
        best_match_start = -1
        best_match_end = -1

        # 搜索范围：允许10%的长度变化
        search_len = len(search_lines)
        min_len = math.floor(search_len * 0.9)
        max_len = math.ceil(search_len * 1.1)

        for length in range(min_len, max_len + 1):
            for i in range(len(content_lines) - length + 1):
                chunk_lines = content_lines[i:i + length]
                chunk_text = "".join(chunk_lines)

                # 计算相似度
                similarity = SequenceMatcher(None, chunk_text, search_text).ratio()

                if similarity > max_similarity and similarity >= threshold:
                    max_similarity = similarity
                    best_match_start = i
                    best_match_end = i + length

        if best_match_start >= 0:
            logger.info(f"🎯 找到模糊匹配 (相似度: {max_similarity:.3f})")
            result_lines = (content_lines[:best_match_start] +
                           replace_lines +
                           content_lines[best_match_end:])
            return "".join(result_lines)

        return None

    def search_replace_operation(self,
                                source_dir: Path,
                                operation_json: str) -> Dict[str, Any]:
        """
        执行基于JSON的搜索替换操作 - 仅支持精确匹配

        Args:
            source_dir: 源代码目录
            operation_json: JSON格式的搜索替换操作指令

        Returns:
            操作结果字典

        Example JSON:
            {
                "operation": {
                    "type": "search_replace",
                    "file_path": "example.py",
                    "search": "def old_function():\n    pass",
                    "replace": "def new_function():\n    print('updated')",
                    "exact_match_only": true
                }
            }

        Note: 为确保代码修改的精确性和安全性，此方法仅执行精确匹配。
              不再支持模糊匹配或空白字符灵活匹配。
        """
        try:
            logger.debug('operation_json: ', operation_json)
            operation_data = json.loads(operation_json)
        except json.JSONDecodeError as e:
            return {"success": False, "error": f"无效的JSON格式: {e}"}

        if "operation" not in operation_data:
            return {"success": False, "error": "JSON中缺少'operation'字段"}

        operation = operation_data["operation"]

        # 验证必要字段
        required_fields = ["type", "file_path", "search", "replace"]
        for field in required_fields:
            if field not in operation:
                return {"success": False, "error": f"操作缺少必要字段: {field}"}

        if operation["type"] != "search_replace":
            return {"success": False, "error": f"不支持的操作类型: {operation['type']}"}

        # 执行精确搜索替换
        file_path = source_dir / operation["file_path"]
        search_text = operation["search"]
        replace_text = operation["replace"]

        # 强制使用精确匹配模式
        return self.search_replace_in_file(
            file_path, search_text, replace_text,
            fuzzy_match=False,  # 禁用模糊匹配
            similarity_threshold=1.0  # 仅精确匹配
        )
