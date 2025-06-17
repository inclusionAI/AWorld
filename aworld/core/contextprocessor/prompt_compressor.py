import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

# Add new dependencies
try:
    import nltk
    from nltk.tokenize import sent_tokenize
    from nltk.corpus import stopwords
    from sklearn.feature_extraction.text import TfidfVectorizer
    import numpy as np
    
    # Download necessary NLTK resources with improved error handling
    try:
        # Check if necessary data has been downloaded
        try:
            nltk.data.find('tokenizers/punkt')
            nltk.data.find('corpora/stopwords')
        except LookupError:
            # If not found, try to download
            try:
                nltk.download('punkt', quiet=True)
                nltk.download('stopwords', quiet=True)
            except Exception as download_error:
                logger.warning(f"NLTK data download failed: {download_error}")
    except Exception as nltk_error:
        logger.warning(f"NLTK initialization failed: {nltk_error}")
    
    TFIDF_AVAILABLE = True
except ImportError:
    TFIDF_AVAILABLE = False

class CompressionType(Enum):
    RULE_BASED = "rule_based"
    STATISTICAL = "statistical"
    LLM_BASED = "llm_based"
    TFIDF_BASED = "tfidf_based"  # New TF-IDF compression type

@dataclass
class CompressionResult:
    """Compression result data structure"""
    original_content: str
    compressed_content: str
    compression_ratio: float
    metadata: Dict[str, Any]
    compression_type: CompressionType

class BaseCompressor(ABC):
    """Base compressor class"""
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        
    @abstractmethod
    def compress(self, content: str, metadata: Dict[str, Any] = None) -> CompressionResult:
        """Compress content"""
        pass
    
    def _calculate_compression_ratio(self, original: str, compressed: str) -> float:
        """Calculate compression ratio"""
        if len(original) == 0:
            return 1.0
        return len(compressed) / len(original)

class RuleBasedCompressor(BaseCompressor):
    """Rule-based compressor"""
    
    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self.rules = self.config.get("rules", self._default_rules())
    
    def _default_rules(self) -> List[Dict[str, Any]]:
        """Default compression rules"""
        return [
            {
                "name": "remove_extra_whitespace",
                "pattern": r'\s+',
                "replacement": ' ',
                "description": "Remove extra whitespace characters"
            },
            {
                "name": "remove_redundant_punctuation",
                "pattern": r'[。！？]{2,}',
                "replacement": '。',
                "description": "Remove redundant punctuation marks"
            },
            {
                "name": "compress_repetitive_phrases",
                "pattern": r'(\b\w+\b)(\s+\1){2,}',
                "replacement": r'\1',
                "description": "Compress repetitive phrases"
            },
            {
                "name": "remove_filler_words",
                "pattern": r'\b(嗯|啊|呃|那个|这个|就是说|然后)\b',
                "replacement": '',
                "description": "Remove filler words"
            }
        ]
    
    def compress(self, content: str, metadata: Dict[str, Any] = None) -> CompressionResult:
        """Compress content based on rules"""
        original_content = content
        compressed_content = content
        
        # Apply all rules
        for rule in self.rules:
            pattern = rule["pattern"]
            replacement = rule["replacement"]
            compressed_content = re.sub(pattern, replacement, compressed_content)
            logger.debug(f"Applied rule {rule['name']}: {rule['description']}")
        
        # Clean leading and trailing whitespace
        compressed_content = compressed_content.strip()
        
        compression_ratio = self._calculate_compression_ratio(original_content, compressed_content)
        
        return CompressionResult(
            original_content=original_content,
            compressed_content=compressed_content,
            compression_ratio=compression_ratio,
            metadata={
                "applied_rules": [rule["name"] for rule in self.rules],
                "original_metadata": metadata or {}
            },
            compression_type=CompressionType.RULE_BASED
        )

class StatisticalCompressor(BaseCompressor):
    """Statistical-based compressor"""
    
    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self.max_sentence_length = self.config.get("max_sentence_length", 100)
        self.keep_ratio = self.config.get("keep_ratio", 0.7)
        self.importance_threshold = self.config.get("importance_threshold", 0.3)
    
    def _calculate_sentence_importance(self, sentence: str, all_sentences: List[str]) -> float:
        """Calculate sentence importance score"""
        # Length-based importance
        length_score = min(len(sentence) / self.max_sentence_length, 1.0)
        
        # Keyword density-based importance
        keywords = ["重要", "关键", "核心", "主要", "首先", "其次", "最后", "因此", "所以"]
        keyword_count = sum(1 for keyword in keywords if keyword in sentence)
        keyword_score = min(keyword_count / 3, 1.0)
        
        # Position-based importance (beginning and end sentences are more important)
        sentence_index = all_sentences.index(sentence) if sentence in all_sentences else 0
        total_sentences = len(all_sentences)
        if total_sentences <= 1:
            position_score = 1.0
        elif sentence_index == 0 or sentence_index == total_sentences - 1:
            position_score = 1.0
        else:
            position_score = 0.5
        
        # Composite score
        importance_score = (length_score * 0.4 + keyword_score * 0.4 + position_score * 0.2)
        return importance_score
    
    def compress(self, content: str, metadata: Dict[str, Any] = None) -> CompressionResult:
        """Compress content based on statistics"""
        original_content = content
        
        # Split into sentences
        sentences = re.split(r'[。！？\n]+', content)
        sentences = [s.strip() for s in sentences if s.strip()]
        
        if not sentences:
            return CompressionResult(
                original_content=original_content,
                compressed_content=content,
                compression_ratio=1.0,
                metadata={"original_metadata": metadata or {}},
                compression_type=CompressionType.STATISTICAL
            )

        # Calculate importance for each sentence
        sentence_scores = []
        for sentence in sentences:
            importance = self._calculate_sentence_importance(sentence, sentences)
            sentence_scores.append((sentence, importance))
        
        # Sort by importance
        sentence_scores.sort(key=lambda x: x[1], reverse=True)
        
        # Select important sentences
        keep_count = max(1, int(len(sentences) * self.keep_ratio))
        selected_sentences = []
        
        for sentence, score in sentence_scores[:keep_count]:
            if score >= self.importance_threshold:
                selected_sentences.append(sentence)
        
        # If no sentences meet the threshold, keep at least the most important one
        if not selected_sentences and sentence_scores:
            selected_sentences.append(sentence_scores[0][0])

        # Reorganize sentences (maintain original order)
        compressed_sentences = []
        for sentence in sentences:
            if sentence in selected_sentences:
                compressed_sentences.append(sentence)
        
        compressed_content = '。'.join(compressed_sentences) + '。' if compressed_sentences else ''

        compression_ratio = self._calculate_compression_ratio(original_content, compressed_content)
        
        return CompressionResult(
            original_content=original_content,
            compressed_content=compressed_content,
            compression_ratio=compression_ratio,
            metadata={
                "selected_sentences": len(compressed_sentences),
                "total_sentences": len(sentences),
                "average_importance": sum(score for _, score in sentence_scores) / len(sentence_scores),
                "original_metadata": metadata or {}
            },
            compression_type=CompressionType.STATISTICAL
        )

class LLMCompressor(BaseCompressor):
    """LLM-based compressor"""
    
    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        self.model_name = self.config.get("model_name", "gpt-3.5-turbo")
        self.max_tokens = self.config.get("max_tokens", 1000)
        self.compression_prompt = self.config.get("compression_prompt", self._default_compression_prompt())
        self.llm_base_url = self.config.get("llm_base_url", "https://api.openai.com/v1/chat/completions")
        # Lazy import to avoid circular dependencies
        self._llm_client = None
    
    def _default_compression_prompt(self) -> str:
        """Default compression prompt"""
        return """## Task
You are a text compression expert. Please intelligently compress the following text, retaining core information and key content while removing redundancy and unimportant parts.

## Compression Requirements
1. Keep the position and count of [SYSTEM], [USER], [ASSISTANT], and [TOOL] tags unchanged in the output
2. Maintain the main meaning and logical structure of the original text, retain key information and important details, use more concise expressions
3. Remove repetitive, redundant statements, ensure the compressed text remains coherent and readable

## Original Text:
{content}

Please output the compressed text:"""
    
    def _get_llm_client(self):
        """Get LLM client"""
        if self._llm_client is None:
            try:
                # Try to import and create LLM client
                from aworld.models.llm import get_llm_model
                from aworld.config import ConfigDict
                
                config = ConfigDict(self.config)
                self._llm_client = get_llm_model(config)
            except ImportError:
                logger.warning("Unable to import LLM module, LLM compression functionality unavailable")
                self._llm_client = None
        return self._llm_client
    
    def compress(self, content: str, metadata: Dict[str, Any] = None) -> CompressionResult:
        """Compress content using LLM"""
        original_content = content
        
        # Get LLM client
        llm_client = self._get_llm_client()
        if llm_client is None:
            logger.warning("LLM client unavailable, returning original content")
            return CompressionResult(
                original_content=original_content,
                compressed_content=content,
                compression_ratio=1.0,
                metadata={"error": "LLM client unavailable", "original_metadata": metadata or {}},
                compression_type=CompressionType.LLM_BASED
            )
        
        try:
            # Build prompt
            prompt = self.compression_prompt.format(content=content)
            messages = [{"role": "user", "content": prompt}]
            
            # Call LLM
            response = llm_client.completion(
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=0.3
            )
            
            compressed_content = response.content.strip()
            compression_ratio = self._calculate_compression_ratio(original_content, compressed_content)
            
            return CompressionResult(
                original_content=original_content,
                compressed_content=compressed_content,
                compression_ratio=compression_ratio,
                metadata={
                    "model_name": self.model_name,
                    "prompt_tokens": getattr(response.usage, 'prompt_tokens', 0),
                    "completion_tokens": getattr(response.usage, 'completion_tokens', 0),
                    "original_metadata": metadata or {}
                },
                compression_type=CompressionType.LLM_BASED
            )
            
        except Exception as e:
            logger.error(f"LLM compression failed: {e}")
            return CompressionResult(
                original_content=original_content,
                compressed_content=content,
                compression_ratio=1.0,
                metadata={"error": str(e), "original_metadata": metadata or {}},
                compression_type=CompressionType.LLM_BASED
            )

class TfidfCompressor(BaseCompressor):
    """TF-IDF-based intelligent compressor, referencing x.py algorithm implementation"""
    
    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config)
        if not TFIDF_AVAILABLE:
            raise ImportError("TF-IDF compressor requires nltk and scikit-learn: pip install nltk scikit-learn")
        
        # Configuration parameters
        self.compression_level = self.config.get("compression_level", "medium")
        self.preserve_format = self.config.get("preserve_format", True)
        self.target_model = self.config.get("target_model", "general")
        
        # Compression ratio settings
        self.compression_ratios = {
            "light": 0.8,    # Keep 80% of content
            "medium": 0.6,   # Keep 60% of content
            "heavy": 0.4     # Keep 40% of content
        }
        self.target_ratio = self.compression_ratios.get(self.compression_level, 0.6)
        
        # Initialize stop words
        try:
            self.stop_words = set(stopwords.words('english'))
            # Add common Chinese stop words
            chinese_stop_words = {'的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都', '一', '一个', '上', '也', '很', '到', '说', '要', '去', '你', '会', '着', '没有', '看', '好', '自己', '这'}
            self.stop_words.update(chinese_stop_words)
        except:
            self.stop_words = set()
    
    def clean_text(self, text: str) -> str:
        """Clean text, remove extra whitespace, referencing x.py implementation"""
        # Preserve code blocks
        code_blocks = []
        if self.preserve_format:
            # Mark code blocks for preservation
            code_blocks = re.findall(r'```[\s\S]*?```', text)
            for i, block in enumerate(code_blocks):
                text = text.replace(block, f"__CODE_BLOCK_{i}__")
        
        # Remove extra blank lines
        text = re.sub(r'\n\s*\n+', '\n\n', text)
        # Normalize spaces
        text = re.sub(r'\s+', ' ', text)
        
        # Restore code blocks
        if self.preserve_format:
            for i, block in enumerate(code_blocks):
                text = text.replace(f"__CODE_BLOCK_{i}__", block)
                
        return text, code_blocks
    
    def split_into_segments(self, text: str) -> List[Dict]:
        """Split text into paragraphs and code blocks, referencing x.py implementation"""
        segments = []
        
        # Separate code blocks and regular text
        if self.preserve_format:
            pattern = r'(```[\s\S]*?```)'
            parts = re.split(pattern, text)
            
            for part in parts:
                if part.startswith('```') and part.endswith('```'):
                    segments.append({"type": "code", "content": part})
                elif part.strip():
                    paragraphs = part.split('\n\n')
                    for p in paragraphs:
                        if p.strip():
                            segments.append({"type": "text", "content": p.strip()})
        else:
            paragraphs = text.split('\n\n')
            for p in paragraphs:
                if p.strip():
                    segments.append({"type": "text", "content": p.strip()})
                    
        return segments
    
    def extract_important_sentences(self, text: str, ratio: float) -> List[str]:
        """Extract important sentences using TF-IDF, referencing x.py implementation"""
        try:
            sentences = sent_tokenize(text)
        except:
            # If nltk sentence splitting fails, use simple regex
            sentences = re.split(r'[。！？\.\!\?]+', text)
            sentences = [s.strip() for s in sentences if s.strip()]
        
        if len(sentences) <= 3:  # Too few sentences, keep all
            return sentences
            
        # Create TF-IDF vectors
        vectorizer = TfidfVectorizer(
            stop_words=list(self.stop_words) if self.stop_words else None,
            max_features=1000,
            ngram_range=(1, 2)  # Use 1-2gram features
        )
        
        try:
            tfidf_matrix = vectorizer.fit_transform(sentences)
            
            # Calculate average TF-IDF score for each sentence
            sentence_scores = np.array([tfidf_matrix[i].mean() for i in range(len(sentences))])
            
            # Get important sentences in original order
            num_to_keep = max(1, int(len(sentences) * ratio))
            top_indices = np.argsort(sentence_scores)[-num_to_keep:]
            top_indices = sorted(top_indices)  # Maintain original order
            
            return [sentences[i] for i in top_indices]
        except Exception as e:
            logger.warning(f"TF-IDF processing failed: {e}, using simple method")
            # If vectorization fails, use simple method
            num_to_keep = max(1, int(len(sentences) * ratio))
            return sentences[:num_to_keep]
    
    def compress_text_segment(self, segment: Dict) -> str:
        """Compress single text segment, referencing x.py implementation"""
        if segment["type"] == "code":
            # Keep code blocks intact
            return segment["content"]
        
        text = segment["content"]
        important_sentences = self.extract_important_sentences(text, self.target_ratio)
        return ' '.join(important_sentences)
    
    def apply_model_specific_optimization(self, text: str) -> str:
        """Apply model-specific optimization, referencing x.py implementation"""
        if self.target_model == "gpt-4":
            # GPT-4 specific optimization
            text = re.sub(r'Please provide .* response\.', '', text)
            text = re.sub(r'请提供.*回答。', '', text)
        elif self.target_model == "claude":
            # Claude specific optimization
            text = re.sub(r'Human: |Assistant: ', '', text)
        elif self.target_model == "chatglm":
            # ChatGLM specific optimization
            text = re.sub(r'用户：|助手：', '', text)
        
        return text
    
    def compress(self, content: str, metadata: Dict[str, Any] = None) -> CompressionResult:
        """Compress entire prompt text, referencing x.py complete implementation"""
        original_content = content
        
        # Clean text
        cleaned_text, code_blocks = self.clean_text(content)
        
        # Split into paragraphs and code blocks
        segments = self.split_into_segments(cleaned_text)
        
        # Compress each paragraph separately
        compressed_segments = []
        text_segments_count = 0
        code_segments_count = 0
        
        for segment in segments:
            compressed_segment = self.compress_text_segment(segment)
            if compressed_segment.strip():  # Only keep non-empty paragraphs
                compressed_segments.append(compressed_segment)
                
                if segment["type"] == "text":
                    text_segments_count += 1
                else:
                    code_segments_count += 1
        
        # Combine compressed paragraphs
        result = '\n\n'.join(compressed_segments)
        
        # Model-specific optimization
        result = self.apply_model_specific_optimization(result)
        
        # Final cleanup
        result = result.strip()
        
        compression_ratio = self._calculate_compression_ratio(original_content, result)
        
        # Calculate detailed statistics
        original_chars = len(original_content)
        compressed_chars = len(result)
        original_words = len(original_content.split())
        compressed_words = len(result.split())
        
        # Estimate token count (rough estimate)
        original_tokens = original_chars / 4
        compressed_tokens = compressed_chars / 4
        
        compression_metadata = {
            "compression_level": self.compression_level,
            "target_ratio": self.target_ratio,
            "preserve_format": self.preserve_format,
            "target_model": self.target_model,
            "original_chars": original_chars,
            "compressed_chars": compressed_chars,
            "original_words": original_words,
            "compressed_words": compressed_words,
            "estimated_original_tokens": int(original_tokens),
            "estimated_compressed_tokens": int(compressed_tokens),
            "text_segments": text_segments_count,
            "code_segments": code_segments_count,
            "code_blocks_preserved": len(code_blocks),
            "space_saved": original_chars - compressed_chars,
            "space_saved_percentage": ((original_chars - compressed_chars) / original_chars * 100) if original_chars > 0 else 0.0,
            "original_metadata": metadata or {}
        }
        
        return CompressionResult(
            original_content=original_content,
            compressed_content=result,
            compression_ratio=compression_ratio,
            metadata=compression_metadata,
            compression_type=CompressionType.TFIDF_BASED
        )

class PromptCompressor:
    """Unified Prompt compressor"""
    
    def __init__(self, compression_types: List[CompressionType] = None, configs: Dict[CompressionType, Dict[str, Any]] = None):
        """
        Initialize compressor
        
        Args:
            compression_types: List of compression types to use
            configs: Configuration for each compressor
        """
        self.compression_types = compression_types or [CompressionType.RULE_BASED]
        self.configs = configs or {}
        
        # Initialize various compressors
        print('debug11111111111111111|', self.configs)
        self.compressors = {}
        for comp_type in self.compression_types:
            config = self.configs.get(comp_type, {})
            if comp_type == CompressionType.RULE_BASED:
                self.compressors[comp_type] = RuleBasedCompressor(config)
            elif comp_type == CompressionType.STATISTICAL:
                self.compressors[comp_type] = StatisticalCompressor(config)
            elif comp_type == CompressionType.LLM_BASED:
                self.compressors[comp_type] = LLMCompressor(config)
            elif comp_type == CompressionType.TFIDF_BASED:
                self.compressors[comp_type] = TfidfCompressor(config)
    
    def compress(self, content: str, metadata: Dict[str, Any] = None, compression_type: CompressionType = None) -> CompressionResult:
        """
        Compress content
        
        Args:
            content: Content to compress
            metadata: Metadata
            compression_type: Specified compression type, if None use first available compressor
            
        Returns:
            Compression result
        """
        if compression_type is None:
            compression_type = self.compression_types[0]
        
        if compression_type not in self.compressors:
            logger.warning(f"Compression type {compression_type} unavailable, using default compressor")
            compression_type = self.compression_types[0]
        
        compressor = self.compressors[compression_type]
        return compressor.compress(content, metadata)
    
    def compress_batch(self, contents: List[str], metadata_list: List[Dict[str, Any]] = None, compression_type: CompressionType = None) -> List[CompressionResult]:
        """
        Batch compress content
        
        Args:
            contents: List of content to compress
            metadata_list: List of metadata
            compression_type: Compression type
            
        Returns:
            List of compression results
        """
        if metadata_list is None:
            metadata_list = [{}] * len(contents)
        
        results = []
        for content, metadata in zip(contents, metadata_list):
            result = self.compress(content, metadata, compression_type)
            results.append(result)
        
        return results
    
    def get_compression_stats(self, results: List[CompressionResult]) -> Dict[str, Any]:
        """
        Get compression statistics
        
        Args:
            results: List of compression results
            
        Returns:
            Statistics information
        """
        if not results:
            return {}
        
        total_original_length = sum(len(r.original_content) for r in results)
        total_compressed_length = sum(len(r.compressed_content) for r in results)
        
        compression_ratios = [r.compression_ratio for r in results]
        avg_compression_ratio = sum(compression_ratios) / len(compression_ratios)
        
        compression_types = [r.compression_type.value for r in results]
        type_counts = {}
        for comp_type in compression_types:
            type_counts[comp_type] = type_counts.get(comp_type, 0) + 1
        
        return {
            "total_items": len(results),
            "total_original_length": total_original_length,
            "total_compressed_length": total_compressed_length,
            "overall_compression_ratio": total_compressed_length / total_original_length if total_original_length > 0 else 1.0,
            "average_compression_ratio": avg_compression_ratio,
            "compression_type_distribution": type_counts,
            "space_saved": total_original_length - total_compressed_length,
            "space_saved_percentage": ((total_original_length - total_compressed_length) / total_original_length * 100) if total_original_length > 0 else 0.0
        }
