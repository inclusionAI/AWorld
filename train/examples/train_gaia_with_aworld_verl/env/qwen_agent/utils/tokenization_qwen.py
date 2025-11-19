# qwen_agent tokenization utilities
import re
from typing import List, Union


class SimpleTokenizer:
    """Simple tokenizer implementation for basic token counting"""
    
    def __init__(self):
        # Simple tokenization rules based on spaces and punctuation
        self.word_pattern = re.compile(r'\b\w+\b|[^\w\s]')
    
    def tokenize(self, text: str) -> List[str]:
        """Tokenize text into tokens"""
        if not text:
            return []
        return self.word_pattern.findall(text)
    
    def convert_tokens_to_string(self, tokens: List[str]) -> str:
        """Convert tokens back to string"""
        return ' '.join(tokens)


# Create global tokenizer instance
tokenizer = SimpleTokenizer()


def count_tokens(text: Union[str, List[str]]) -> int:
    """Count tokens in text"""
    if isinstance(text, list):
        text = ' '.join(text)
    if not text:
        return 0
    return len(tokenizer.tokenize(text))
