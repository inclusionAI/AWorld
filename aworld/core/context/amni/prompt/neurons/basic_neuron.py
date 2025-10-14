import inspect
from datetime import datetime
from typing import List

from ... import ApplicationContext
from . import Neuron


class BasicNeuron(Neuron):
    """处理动态变量相关属性的Neuron"""
    
    async def format_items(self, context: ApplicationContext, namespace: str = None, **kwargs) -> List[str]:
        """格式化动态变量信息（同步版本，处理同步函数）"""
        items = []
        
        try:
            # 导入动态变量
            from aworldspace.prompt.prompt_ext import ALL_PREDEFINED_DYNAMIC_VARIABLES

            target_keys = {
                "current_date"
            }
            # 排除无用的key
            excluded_keys = {
                'system_platform', 'system_os', 'python_version', 
                'hostname', 'username', 'working_directory', 
                'random_uuid', 'short_uuid',

            }
            
            # 处理预定义的动态变量
            for key, value in ALL_PREDEFINED_DYNAMIC_VARIABLES.items():
                if key not in target_keys:
                    continue
                # 跳过被排除的key
                if key in excluded_keys:
                    continue
                try:
                    if callable(value):
                        # 检查函数签名
                        sig = inspect.signature(value)
                        is_async = inspect.iscoroutinefunction(value)
                        
                        # 只处理同步函数
                        if not is_async:
                            if "context" in sig.parameters:
                                # 同步函数，需要context参数
                                try:
                                    result = value(context=context)
                                    if result:
                                        items.append(f"<{key}>{result}</{key}>")
                                except Exception as e:
                                    items.append(f"<{key}>error: {str(e)}</{key}>")
                            else:
                                # 同步函数，无context参数
                                try:
                                    result = value()
                                    if result:
                                        items.append(f"<{key}>{result}</{key}>")
                                except Exception as e:
                                    items.append(f"<{key}>error: {str(e)}</{key}>")
                        else:
                            # 异步函数，在同步方法中标记为需要异步处理
                            items.append(f"<{key}>async_function_requires_async_context</{key}>")
                    else:
                        # 非函数值
                        if value:
                            items.append(f"<{key}>{value}</{key}>")
                except Exception as e:
                    items.append(f"<{key}>error: {str(e)}</{key}>")
        
        except ImportError:
            items.append("<dynamic_var_error>Cannot import CONTEXT_PREDEFINED_DYNAMIC_VARIABLES</dynamic_var_error>")
        except Exception as e:
            items.append(f"<dynamic_var_error>Error processing dynamic variables: {str(e)}</dynamic_var_error>")
        
        return items
    
    async def format(self, context: ApplicationContext, items: List[str] = None, namespace: str = None, **kwargs) -> str:
        """组合动态变量信息"""
        if not items:
            items = await self.format_items(context, namespace, **kwargs)
        
        return f"今年是{datetime.now().strftime('%Y')}年, Today is： {datetime.now().strftime('%Y-%m-%d')}, please keep in touch."
