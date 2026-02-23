"""文档处理器基类"""

from abc import ABC, abstractmethod


class BaseHandler(ABC):
    """文档处理器基类（支持解析）"""

    @abstractmethod
    def get_supported_types(self) -> list[str]:
        """返回支持的文件类型列表"""
        pass

    @abstractmethod
    def can_handle(self, file_type: str) -> bool:
        """是否能处理指定类型"""
        pass

    @abstractmethod
    def get_handler_type(self) -> str:
        """返回处理器类型：'parser' 或 'converter'"""
        pass

