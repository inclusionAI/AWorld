"""
NLTK文本处理工具类
提供实体识别和关键词提取功能，支持并发处理和批量操作

主要特性:
- 🔍 实体识别: 支持人员、组织机构、地理位置等多种实体类型
- 📊 关键词提取: 提供TF-IDF、频率分析、POS标签三种方法
- ⚡ 并发处理: 支持异步和批量处理，提高效率
- 📦 结构化结果: 使用dataclass提供类型安全的结果存储
- 🔄 向后兼容: 提供字典格式转换，保持兼容性

使用示例:

1. 基本使用:
```python
from amnicontext.utils.nltk_utils import process_text_with_nltk

text = "Apple Inc. was founded by Steve Jobs in Cupertino, California."
result = process_text_with_nltk(text)

print(f"人员: {result.entities.persons}")  # ['Steve Jobs']
print(f"组织机构: {result.entities.organizations}")  # ['Apple Inc.']
print(f"地理位置: {result.entities.locations}")  # ['Cupertino', 'California']
print(f"TF-IDF关键词: {result.keywords.get_top_tfidf(5)}")
```

2. 异步处理:
```python
import asyncio
from amnicontext.utils.nltk_utils import process_text_with_nltk_async

async def process_text():
    result = await process_text_with_nltk_async(text)
    return result

result = asyncio.run(process_text())
```

3. 批量处理:
```python
from amnicontext.utils.nltk_utils import NLTKProcessor

processor = NLTKProcessor(max_workers=4)
texts = ["文本1", "文本2", "文本3"]

# 批量异步处理
results = await processor.process_texts_batch(texts)
```

4. 自定义参数:
```python
processor = NLTKProcessor(max_workers=4)
result = processor.process_text_sync(
    text,
    max_tfidf_features=50,  # TF-IDF最大特征数
    top_frequency=30,       # 频率分析前N个
    top_pos=30             # POS分析前N个
)
```

5. 在ExtractArtifactEntityOp中使用:
```python
# 单文档处理
result = await extract_op.execute(context, event)

# 批量处理
results = await extract_op.execute_batch(context, events)
```

依赖要求:
- nltk
- scikit-learn
- numpy

确保已安装NLTK数据包:
```python
import nltk
nltk.download('punkt')
nltk.download('averaged_perceptron_tagger')
nltk.download('maxent_ne_chunker')
nltk.download('words')
nltk.download('stopwords')
nltk.download('wordnet')
nltk.download('omw-1.4')
```
"""

from typing import Dict, List, Tuple, Optional, Any
import asyncio
import concurrent.futures
import re
from collections import Counter
from dataclasses import dataclass, field

import nltk
from nltk.tokenize import word_tokenize, sent_tokenize
from nltk.tag import pos_tag
from nltk.chunk import ne_chunk
from nltk.corpus import stopwords
from nltk.stem import WordNetLemmatizer
from nltk.corpus import wordnet
from sklearn.feature_extraction.text import TfidfVectorizer
import numpy as np


@dataclass
class EntityExtractionResult:
    """
    Dataclass for storing entity extraction results
    
    存储实体识别结果的dataclass，包含各种类型的实体
    
    示例:
    ```python
    result = EntityExtractionResult()
    result.persons = ['Steve Jobs', 'Bill Gates']
    result.organizations = ['Apple Inc.', 'Microsoft']
    result.locations = ['Cupertino', 'Seattle']
    
    # 转换为字典格式
    dict_result = result.to_dict()
    ```
    """
    persons: List[str] = field(default_factory=list)
    organizations: List[str] = field(default_factory=list)
    locations: List[str] = field(default_factory=list)
    facilities: List[str] = field(default_factory=list)
    money: List[str] = field(default_factory=list)
    percentages: List[str] = field(default_factory=list)
    dates: List[str] = field(default_factory=list)
    times: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, List[str]]:
        """Convert to dictionary format for backward compatibility"""
        return {
            'PERSON': self.persons,
            'ORGANIZATION': self.organizations,
            'GPE': self.locations,
            'FACILITY': self.facilities,
            'MONEY': self.money,
            'PERCENT': self.percentages,
            'DATE': self.dates,
            'TIME': self.times
        }


@dataclass
class KeywordExtractionResult:
    """
    Dataclass for storing keyword extraction results
    
    存储关键词提取结果的dataclass，包含三种不同的关键词提取方法
    
    示例:
    ```python
    result = KeywordExtractionResult()
    result.tfidf_keywords = [('apple', 0.8), ('technology', 0.6)]
    result.frequency_keywords = [('company', 5), ('product', 3)]
    result.pos_keywords = [('innovation', 2), ('development', 2)]
    
    # 获取前N个关键词
    top_tfidf = result.get_top_tfidf(5)
    top_freq = result.get_top_frequency(5)
    ```
    """
    tfidf_keywords: List[Tuple[str, float]] = field(default_factory=list)
    frequency_keywords: List[Tuple[str, int]] = field(default_factory=list)
    pos_keywords: List[Tuple[str, int]] = field(default_factory=list)
    
    def get_top_tfidf(self, top_n: int = 10) -> List[Tuple[str, float]]:
        """Get top N TF-IDF keywords"""
        return self.tfidf_keywords[:top_n]
    
    def get_top_frequency(self, top_n: int = 10) -> List[Tuple[str, int]]:
        """Get top N frequency keywords"""
        return self.frequency_keywords[:top_n]
    
    def get_top_pos(self, top_n: int = 10) -> List[Tuple[str, int]]:
        """Get top N POS-based keywords"""
        return self.pos_keywords[:top_n]


@dataclass
class NLTKProcessingResult:
    """
    Main dataclass for storing all NLTK processing results
    
    主要的NLTK处理结果存储类，包含实体识别和关键词提取的所有结果
    
    示例:
    ```python
    processor = NLTKProcessor()
    result = processor.process_text_sync("Apple Inc. was founded by Steve Jobs.")
    
    # 访问实体信息
    print(result.entities.persons)  # ['Steve Jobs']
    print(result.entities.organizations)  # ['Apple Inc.']
    
    # 访问关键词信息
    print(result.keywords.get_top_tfidf(5))
    
    # 访问元数据
    print(result.metadata['text_length'])
    
    # 转换为字典格式（向后兼容）
    dict_result = result.to_dict()
    ```
    """
    entities: EntityExtractionResult = field(default_factory=EntityExtractionResult)
    keywords: KeywordExtractionResult = field(default_factory=KeywordExtractionResult)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format for backward compatibility"""
        return {
            'entities': self.entities.to_dict(),
            'keywords_tfidf': self.keywords.tfidf_keywords,
            'keywords_frequency': self.keywords.frequency_keywords,
            'keywords_pos': self.keywords.pos_keywords,
            'metadata': self.metadata
        }


class NLTKProcessor:
    """
    NLTK-based text processing utility class
    Supports concurrent processing and batch operations for better performance
    """
    
    def __init__(self, max_workers: int = 4):
        """
        Initialize NLTK processor
        
        Args:
            max_workers: Maximum number of worker threads for concurrent processing
        """
        self._nltk_initialized = False
        self._stop_words: Optional[set] = None
        self._lemmatizer: Optional[WordNetLemmatizer] = None
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    
    def _ensure_nltk_initialized(self) -> None:
        """Initialize NLTK resources if not already done"""
        if self._nltk_initialized:
            return
            
        try:
            # Download required NLTK data if needed
            # nltk.download('punkt', quiet=True)
            # nltk.download('averaged_perceptron_tagger', quiet=True)
            # nltk.download('maxent_ne_chunker', quiet=True)
            # nltk.download('words', quiet=True)
            # nltk.download('stopwords', quiet=True)
            # nltk.download('wordnet', quiet=True)
            # nltk.download('omw-1.4', quiet=True)
            
            self._stop_words = set(stopwords.words('english'))
            self._lemmatizer = WordNetLemmatizer()
            self._nltk_initialized = True
        except Exception as e:
            raise RuntimeError(f"Failed to initialize NLTK resources: {e}")
    
    def _get_wordnet_pos(self, treebank_tag: str) -> str:
        """Convert POS tag to wordnet format for lemmatization"""
        if treebank_tag.startswith('J'):
            return wordnet.ADJ
        elif treebank_tag.startswith('V'):
            return wordnet.VERB
        elif treebank_tag.startswith('N'):
            return wordnet.NOUN
        elif treebank_tag.startswith('R'):
            return wordnet.ADV
        else:
            return wordnet.NOUN
    
    def extract_entities(self, text: str) -> EntityExtractionResult:
        """
        Extract named entities using NLTK
        
        Args:
            text: Input text to process
            
        Returns:
            EntityExtractionResult containing extracted entities
        """
        self._ensure_nltk_initialized()
        
        try:
            # Tokenize and tag
            tokens = word_tokenize(text)
            pos_tags = pos_tag(tokens)
            
            # Extract named entities
            chunks = ne_chunk(pos_tags, binary=False)
            
            entities = EntityExtractionResult()
            
            for chunk in chunks:
                if hasattr(chunk, 'label'):
                    # Named entity chunk
                    entity_text = ' '.join([token for token, pos in chunk.leaves()])
                    entity_type = chunk.label()
                    
                    # Map entity types to our categories
                    if entity_type in ['PERSON']:
                        entities.persons.append(entity_text)
                    elif entity_type in ['ORG', 'ORGANIZATION']:
                        entities.organizations.append(entity_text)
                    elif entity_type in ['GPE', 'LOCATION']:
                        entities.locations.append(entity_text)
                    elif entity_type in ['FACILITY']:
                        entities.facilities.append(entity_text)
                    else:
                        entities.facilities.append(entity_text)
                else:
                    # Regular token - check for special patterns
                    token, pos = chunk
                    if pos in ['CD']:  # Cardinal number
                        if re.match(r'\d+%', token):
                            entities.percentages.append(token)
                        elif re.match(r'[$€£¥]\d+', token):
                            entities.money.append(token)
                        else:
                            entities.dates.append(token)
            
            # Remove duplicates while preserving order
            entities.persons = list(dict.fromkeys(entities.persons))
            entities.organizations = list(dict.fromkeys(entities.organizations))
            entities.locations = list(dict.fromkeys(entities.locations))
            entities.facilities = list(dict.fromkeys(entities.facilities))
            entities.money = list(dict.fromkeys(entities.money))
            entities.percentages = list(dict.fromkeys(entities.percentages))
            entities.dates = list(dict.fromkeys(entities.dates))
            entities.times = list(dict.fromkeys(entities.times))
                
            return entities
        except Exception as e:
            raise RuntimeError(f"Error extracting entities: {e}")
    
    def extract_keywords_tfidf(self, text: str, max_features: int = 50) -> List[Tuple[str, float]]:
        """
        Extract keywords using TF-IDF
        
        Args:
            text: Input text to process
            max_features: Maximum number of features to extract
            
        Returns:
            List of (keyword, score) tuples sorted by score
        """
        try:
            # Clean and preprocess text
            sentences = sent_tokenize(text)
            cleaned_sentences = []
            
            for sentence in sentences:
                # Remove special characters and convert to lowercase
                cleaned = re.sub(r'[^a-zA-Z\s]', '', sentence.lower())
                cleaned_sentences.append(cleaned)
            
            # Use TF-IDF vectorizer
            vectorizer = TfidfVectorizer(
                max_features=max_features,
                stop_words='english',
                ngram_range=(1, 2),  # Include unigrams and bigrams
                min_df=1,
                max_df=0.8
            )
            
            tfidf_matrix = vectorizer.fit_transform(cleaned_sentences)
            feature_names = vectorizer.get_feature_names_out()
            
            # Get mean TF-IDF scores
            mean_scores = np.mean(tfidf_matrix.toarray(), axis=0)
            
            # Create keyword-score pairs
            keywords = list(zip(feature_names, mean_scores))
            keywords.sort(key=lambda x: x[1], reverse=True)
            
            return keywords[:max_features]
        except Exception as e:
            raise RuntimeError(f"Error extracting keywords with TF-IDF: {e}")
    
    def extract_keywords_frequency(self, text: str, top_n: int = 30) -> List[Tuple[str, int]]:
        """
        Extract keywords using frequency analysis
        
        Args:
            text: Input text to process
            top_n: Number of top keywords to return
            
        Returns:
            List of (keyword, frequency) tuples sorted by frequency
        """
        self._ensure_nltk_initialized()
        
        try:
            # Tokenize and clean
            tokens = word_tokenize(text.lower())
            
            # Remove stopwords and non-alphabetic tokens
            filtered_tokens = [
                token for token in tokens 
                if token.isalpha() and token not in self._stop_words and len(token) > 2
            ]
            
            # Count frequencies
            word_freq = Counter(filtered_tokens)
            
            return word_freq.most_common(top_n)
        except Exception as e:
            raise RuntimeError(f"Error extracting keywords with frequency: {e}")
    
    def extract_keywords_pos_based(self, text: str, top_n: int = 30) -> List[Tuple[str, int]]:
        """
        Extract keywords based on POS tags (nouns, adjectives, verbs)
        
        Args:
            text: Input text to process
            top_n: Number of top keywords to return
            
        Returns:
            List of (keyword, frequency) tuples sorted by frequency
        """
        self._ensure_nltk_initialized()
        
        try:
            tokens = word_tokenize(text)
            pos_tags = pos_tag(tokens)
            
            # Filter for important POS tags
            important_pos = ['NN', 'NNS', 'NNP', 'NNPS', 'JJ', 'JJR', 'JJS', 'VB', 'VBD', 'VBG', 'VBN', 'VBP', 'VBZ']
            
            filtered_tokens = [
                token.lower() for token, pos in pos_tags
                if pos in important_pos and token.isalpha() and len(token) > 2 and token.lower() not in self._stop_words
            ]
            
            word_freq = Counter(filtered_tokens)
            return word_freq.most_common(top_n)
        except Exception as e:
            raise RuntimeError(f"Error extracting POS-based keywords: {e}")
    
    async def process_text_async(self, text: str, max_tfidf_features: int = 50, 
                                top_frequency: int = 30, top_pos: int = 30) -> NLTKProcessingResult:
        """
        Process text asynchronously for better performance
        
        Args:
            text: Input text to process
            max_tfidf_features: Maximum TF-IDF features to extract
            top_frequency: Number of top frequency keywords
            top_pos: Number of top POS-based keywords
            
        Returns:
            NLTKProcessingResult containing all extracted information
        """
        loop = asyncio.get_event_loop()
        
        # Run CPU-intensive tasks in thread pool
        with self._executor as executor:
            entities_task = loop.run_in_executor(executor, self.extract_entities, text)
            tfidf_task = loop.run_in_executor(executor, self.extract_keywords_tfidf, text, max_tfidf_features)
            freq_task = loop.run_in_executor(executor, self.extract_keywords_frequency, text, top_frequency)
            pos_task = loop.run_in_executor(executor, self.extract_keywords_pos_based, text, top_pos)
            
            # Wait for all tasks to complete
            entities, tfidf_keywords, freq_keywords, pos_keywords = await asyncio.gather(
                entities_task, tfidf_task, freq_task, pos_task
            )
        
        # Create result objects
        keyword_result = KeywordExtractionResult(
            tfidf_keywords=tfidf_keywords,
            frequency_keywords=freq_keywords,
            pos_keywords=pos_keywords
        )
        
        metadata = {
            'text_length': len(text),
            'processing_method': 'nltk',
            'max_tfidf_features': max_tfidf_features,
            'top_frequency': top_frequency,
            'top_pos': top_pos
        }
        
        return NLTKProcessingResult(
            entities=entities,
            keywords=keyword_result,
            metadata=metadata
        )
    
    def process_text_sync(self, text: str, max_tfidf_features: int = 50, 
                         top_frequency: int = 30, top_pos: int = 30) -> NLTKProcessingResult:
        """
        Process text synchronously
        
        Args:
            text: Input text to process
            max_tfidf_features: Maximum TF-IDF features to extract
            top_frequency: Number of top frequency keywords
            top_pos: Number of top POS-based keywords
            
        Returns:
            NLTKProcessingResult containing all extracted information
        """
        # Extract entities
        entities = self.extract_entities(text)
        
        # Extract keywords
        tfidf_keywords = self.extract_keywords_tfidf(text, max_tfidf_features)
        freq_keywords = self.extract_keywords_frequency(text, top_frequency)
        pos_keywords = self.extract_keywords_pos_based(text, top_pos)
        
        # Create result objects
        keyword_result = KeywordExtractionResult(
            tfidf_keywords=tfidf_keywords,
            frequency_keywords=freq_keywords,
            pos_keywords=pos_keywords
        )
        
        metadata = {
            'text_length': len(text),
            'processing_method': 'nltk',
            'max_tfidf_features': max_tfidf_features,
            'top_frequency': top_frequency,
            'top_pos': top_pos
        }
        
        return NLTKProcessingResult(
            entities=entities,
            keywords=keyword_result,
            metadata=metadata
        )
    
    async def process_texts_batch(self, texts: List[str], max_tfidf_features: int = 50, 
                                 top_frequency: int = 30, top_pos: int = 30) -> List[NLTKProcessingResult]:
        """
        Process multiple texts concurrently in batches
        
        Args:
            texts: List of texts to process
            max_tfidf_features: Maximum TF-IDF features to extract
            top_frequency: Number of top frequency keywords
            top_pos: Number of top POS-based keywords
            
        Returns:
            List of NLTKProcessingResult objects
        """
        tasks = [
            self.process_text_async(text, max_tfidf_features, top_frequency, top_pos)
            for text in texts
        ]
        
        return await asyncio.gather(*tasks)
    
    def __del__(self):
        """Clean up thread pool executor"""
        if hasattr(self, '_executor'):
            self._executor.shutdown(wait=False)


# Convenience function for quick usage
def process_text_with_nltk(text: str, max_tfidf_features: int = 50, 
                          top_frequency: int = 30, top_pos: int = 30) -> NLTKProcessingResult:
    """
    Convenience function for processing text with NLTK
    
    便利函数，用于快速处理文本的实体识别和关键词提取
    
    Args:
        text: Input text to process
        max_tfidf_features: Maximum TF-IDF features to extract
        top_frequency: Number of top frequency keywords
        top_pos: Number of top POS-based keywords
        
    Returns:
        NLTKProcessingResult containing all extracted information
        
    示例:
    ```python
    text = "Apple Inc. was founded by Steve Jobs in Cupertino, California."
    result = process_text_with_nltk(text)
    
    # 访问结果
    print(f"人员: {result.entities.persons}")  # ['Steve Jobs']
    print(f"组织机构: {result.entities.organizations}")  # ['Apple Inc.']
    print(f"地理位置: {result.entities.locations}")  # ['Cupertino', 'California']
    print(f"TF-IDF关键词: {result.keywords.get_top_tfidf(5)}")
    
    # 自定义参数
    result = process_text_with_nltk(text, max_tfidf_features=20, top_frequency=15)
    ```
    """
    processor = NLTKProcessor()
    return processor.process_text_sync(text, max_tfidf_features, top_frequency, top_pos)


async def process_text_with_nltk_async(text: str, max_tfidf_features: int = 50, 
                                      top_frequency: int = 30, top_pos: int = 30) -> NLTKProcessingResult:
    """
    Convenience function for async processing text with NLTK
    
    异步便利函数，用于异步处理文本的实体识别和关键词提取
    
    Args:
        text: Input text to process
        max_tfidf_features: Maximum TF-IDF features to extract
        top_frequency: Number of top frequency keywords
        top_pos: Number of top POS-based keywords
        
    Returns:
        NLTKProcessingResult containing all extracted information
        
    示例:
    ```python
    import asyncio
    
    async def process_text():
        text = "Tesla Inc. was founded by Elon Musk in California."
        result = await process_text_with_nltk_async(text)
        
        print(f"人员: {result.entities.persons}")  # ['Elon Musk']
        print(f"组织机构: {result.entities.organizations}")  # ['Tesla Inc.']
        print(f"地理位置: {result.entities.locations}")  # ['California']
        
        return result
    
    # 运行异步函数
    result = asyncio.run(process_text())
    ```
    """
    processor = NLTKProcessor()
    return await processor.process_text_async(text, max_tfidf_features, top_frequency, top_pos)
