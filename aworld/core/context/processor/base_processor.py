from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class BaseProcessor(ABC):

    @abstractmethod
    def process_messages(self, messages: List[Dict[str, Any]], **kwargs) -> List[Dict[str, Any]]:
        """
        Abstract method for processing message lists
        
        Args:
            messages: Input message list, each message is a dictionary containing role and content
            **kwargs: Additional processing parameters
            
        Returns:
            Processed message list
            
        Raises:
            NotImplementedError: Subclasses must implement this method
        """
        raise NotImplementedError("Subclasses must implement the process_messages method")
