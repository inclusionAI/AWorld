from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class BaseProcessor(ABC):

    @abstractmethod
    def process_messages(self, messages: List[Dict[str, Any]], **kwargs) -> List[Dict[str, Any]]:
        """
        处理消息列表的抽象方法
        
        Args:
            messages: 输入的消息列表，每个消息是包含role和content的字典
            **kwargs: 额外的处理参数
            
        Returns:
            处理后的消息列表
            
        Raises:
            NotImplementedError: 子类必须实现此方法
        """
        raise NotImplementedError("子类必须实现 process_messages 方法")
