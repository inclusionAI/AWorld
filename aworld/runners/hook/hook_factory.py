# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os
import sys
from pathlib import Path
from typing import Dict, List, Any

import yaml

from aworld.core.factory import Factory
from aworld.core.security.trust import WorkspaceTrust
from aworld.logs.util import logger
from aworld.runners.hook.hooks import Hook, StartHook, HookPoint


class HookManager(Factory):
    """Hook 管理器（增强版）

    支持：
    - Python hooks（通过 @HookFactory.register 装饰器注册）
    - 配置文件 hooks（通过 .aworld/hooks.yaml 配置）
    - Hook 去重（避免重复执行）
    - 配置缓存（避免重复解析 YAML）
    """

    # P0-4: 配置 hooks 缓存（按路径隔离）
    # 格式: {config_path: {'hooks': Dict[str, List[Hook]], 'mtime': float}}
    _config_hooks_cache: Dict[str, Dict[str, Any]] = {}

    def __init__(self, type_name: str = None):
        super(HookManager, self).__init__(type_name)

    def __call__(self, name: str, **kwargs):
        if name is None:
            raise ValueError("hook name is None")

        try:
            if name in self._cls:
                act = self._cls[name](**kwargs)
            else:
                raise RuntimeError("The hook was not registered.\nPlease confirm the package has been imported.")
        except Exception:
            err = sys.exc_info()
            logger.warning(f"Failed to create hook with name {name}:\n{err[1]}")
            act = None
        return act

    @staticmethod
    def _is_standard_workspace_config_path(config_path: str) -> bool:
        path = Path(config_path)
        return path.name == 'hooks.yaml' and path.parent.name == '.aworld'

    @staticmethod
    def load_config_hooks(
        config_path: str = ".aworld/hooks.yaml"
    ) -> Dict[str, List[Hook]]:
        """从配置文件加载 hooks

        Args:
            config_path: 配置文件路径，默认 .aworld/hooks.yaml

        Returns:
            Hook 字典 {hook_point: [Hook, ...]}

        Example:
            >>> hooks = HookFactory.load_config_hooks(".aworld/hooks.yaml")
            >>> hooks['before_tool_call']
            [CommandHookWrapper(...), ...]
        """
        # 规范化路径（解决 /var vs /private/var 等软链接问题）
        config_path = os.path.realpath(config_path)

        # 检查文件是否存在
        if not os.path.exists(config_path):
            logger.debug(f"Hook config file not found: {config_path}")
            return {}

        # P0 Security: Check workspace trust before loading config hooks
        workspace_path = WorkspaceTrust.get_workspace_from_config_path(config_path)
        if workspace_path and not WorkspaceTrust.is_trusted(workspace_path):
            logger.warning(
                f"Workspace is not trusted: {workspace_path}. "
                f"Config hooks from {config_path} will NOT be loaded. "
                f"To trust this workspace: touch {workspace_path}/.aworld/trusted"
            )
            return {}

        # P0-4: 检查路径隔离的缓存
        current_mtime = os.path.getmtime(config_path)
        if config_path in HookManager._config_hooks_cache:
            cached_entry = HookManager._config_hooks_cache[config_path]
            if cached_entry['mtime'] == current_mtime:
                logger.debug(f"Using cached hooks from {config_path}")
                return cached_entry['hooks']

        # 加载配置文件
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
        except Exception as e:
            logger.error(f"Failed to load hooks config from {config_path}: {e}")
            return {}

        if not config or 'hooks' not in config:
            logger.warning(f"No hooks defined in {config_path}")
            return {}

        # 解析 hooks
        hooks = {}
        for hook_point, hook_configs in config.get('hooks', {}).items():
            hooks[hook_point] = []

            for hc in hook_configs:
                # 检查 enabled 字段
                if not hc.get('enabled', True):
                    logger.debug(f"Hook '{hc.get('name', 'unnamed')}' is disabled, skipping")
                    continue

                # 添加 hook_point 到配置
                hc['hook_point'] = hook_point

                # 根据类型创建 hook
                try:
                    if hc.get('type') == 'command':
                        from aworld.runners.hook.v2.wrappers import CommandHookWrapper
                        hooks[hook_point].append(CommandHookWrapper(hc))
                    elif hc.get('type') == 'callback':
                        from aworld.runners.hook.v2.wrappers import CallbackHookWrapper
                        hooks[hook_point].append(CallbackHookWrapper(hc))
                    else:
                        logger.warning(
                            f"Unknown hook type '{hc.get('type')}' for hook '{hc.get('name')}', skipping"
                        )
                except Exception as e:
                    logger.error(
                        f"Failed to create hook '{hc.get('name')}' at point '{hook_point}': {e}"
                    )

        # P0-4: 更新路径隔离的缓存
        HookManager._config_hooks_cache[config_path] = {
            'hooks': hooks,
            'mtime': current_mtime,
            'is_standard_workspace_config': HookManager._is_standard_workspace_config_path(config_path),
        }

        logger.info(
            f"Loaded {sum(len(v) for v in hooks.values())} hooks from {config_path} "
            f"across {len(hooks)} hook points"
        )
        return hooks

    @staticmethod
    def _compute_hook_fingerprint(hook: Hook) -> str:
        """计算 hook 指纹用于去重

        Args:
            hook: Hook 实例

        Returns:
            唯一指纹字符串
        """
        from aworld.runners.hook.v2.wrappers import CommandHookWrapper, CallbackHookWrapper

        if isinstance(hook, CommandHookWrapper):
            # Command hook: hook_point + command + shell
            return f"{hook.point()}:command:{hook._command}:{hook._shell}"
        elif isinstance(hook, CallbackHookWrapper):
            # Callback hook: hook_point + callback path
            return f"{hook.point()}:callback:{hook._callback_path}"
        else:
            # Python hook: hook_point + 类名
            return f"{hook.point()}:python:{hook.__class__.__name__}"

    @staticmethod
    def _deduplicate_hooks(hooks: List[Hook]) -> List[Hook]:
        """按指纹去重，保留第一次出现的 hook

        Args:
            hooks: Hook 列表

        Returns:
            去重后的 Hook 列表
        """
        seen = set()
        unique = []

        for hook in hooks:
            fingerprint = HookManager._compute_hook_fingerprint(hook)
            if fingerprint not in seen:
                seen.add(fingerprint)
                unique.append(hook)
            else:
                logger.debug(f"Duplicate hook skipped: {fingerprint}")

        return unique

    def hooks(self, name: str = None, workspace_path: str = None) -> Dict[str, List[Hook]]:
        """获取所有 hooks（合并 Python hooks 和当前工作区的配置 hooks）

        Args:
            name: Hook 点名称（可选），如果指定则只返回该点的 hooks
            workspace_path: 工作区路径（可选），默认为当前工作目录

        Returns:
            Hook 字典 {hook_point: [Hook, ...]}

        Example:
            >>> hooks = HookFactory.hooks("before_tool_call")
            >>> hooks['before_tool_call']
            [PythonHook(...), CommandHook(...), ...]
        """
        # 1. 获取 Python hooks（原有逻辑）
        vals = list(filter(lambda s: not s.startswith('__'), dir(HookPoint)))
        results = {val.lower(): [] for val in vals}

        for k, v in self._cls.items():
            hook = v()
            if name and hook.point() != name:
               continue

            # 修复：直接访问 results[hook.point()]，因为所有 points 已初始化
            point = hook.point()
            if point in results:
                results[point].append(hook)
            else:
                # 如果 hook point 不在预定义列表中（不太可能），创建新列表
                logger.warning(f"Unknown hook point: {point}, adding to results")
                results[point] = [hook]

        workspace_path_provided = workspace_path is not None

        # 2. P0-1: 自动加载当前工作区的配置文件（如果存在且未加载）
        if workspace_path is None:
            workspace_path = os.getcwd()

        # 构建当前工作区的配置路径（规范化以匹配 load_config_hooks 中的路径）
        current_config_path = os.path.realpath(os.path.join(workspace_path, '.aworld', 'hooks.yaml'))

        # P0-1: 自动加载配置（如果文件存在且未加载）
        if os.path.exists(current_config_path) and current_config_path not in HookManager._config_hooks_cache:
            logger.debug(f"P0-1: Auto-loading hooks config from {current_config_path}")
            HookManager.load_config_hooks(current_config_path)

        # 3. P0-4: 只合并当前工作区的配置 hooks（不污染其他工作区）

        # 查找当前工作区的配置 hooks
        config_hooks = None

        # 策略 1: 优先使用标准路径（生产场景）
        if current_config_path in HookManager._config_hooks_cache:
            cached_entry = HookManager._config_hooks_cache[current_config_path]
            config_hooks = cached_entry['hooks']

        # 策略 2: 当缓存中只有一个显式加载的非标准配置时，允许窄回退
        # 适用场景：
        # - 显式 load_config_hooks(path) 后再调用 hooks()
        # - 运行时 context.workspace_path 尚未设置，但只有一个活动配置
        # 这样既恢复单配置场景，又避免多工作区缓存污染。
        else:
            nonstandard_configs = [
                (path, entry)
                for path, entry in HookManager._config_hooks_cache.items()
                if not entry.get('is_standard_workspace_config', HookManager._is_standard_workspace_config_path(path))
            ]
            if len(nonstandard_configs) == 1:
                fallback_path, cached_entry = nonstandard_configs[0]
                logger.debug(
                    f"Config path {current_config_path} not found in cache. "
                    f"Using the only cached nonstandard hooks from {fallback_path}."
                )
                config_hooks = cached_entry['hooks']
            elif not workspace_path_provided and len(HookManager._config_hooks_cache) == 1:
                fallback_path, cached_entry = next(iter(HookManager._config_hooks_cache.items()))
                logger.debug(
                    f"Config path {current_config_path} not found in cache and workspace_path was not explicit. "
                    f"Using the only cached hooks from {fallback_path} for backward compatibility."
                )
                config_hooks = cached_entry['hooks']
            elif len(HookManager._config_hooks_cache) > 0:
                logger.debug(
                    f"Config path {current_config_path} not found in cache "
                    f"(cached paths: {list(HookManager._config_hooks_cache.keys())}). "
                    f"Maintaining strict workspace isolation - returning Python hooks only."
                )
            # config_hooks 保持 None，只返回 Python hooks

        # 4. 合并配置 hooks（如果找到）
        if config_hooks:
            for point, hooks_list in config_hooks.items():
                if point in results:
                    # Python hooks 在前，config hooks 在后
                    results[point].extend(hooks_list)
                else:
                    # 新的 hook 点（config 独有）
                    results[point] = hooks_list

        # 4. 去重
        for point in results:
            if results[point]:
                results[point] = self._deduplicate_hooks(results[point])

        return results


HookFactory = HookManager("hook_type")
