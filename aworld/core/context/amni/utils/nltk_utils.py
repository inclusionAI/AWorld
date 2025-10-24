"""
NLTK Text Processing Utility Class
Provides entity recognition and keyword extraction functionality with support for concurrent processing and batch operations

Main Features:
- 🔍 Entity Recognition: Supports multiple entity types including persons, organizations, locations, etc.
- 📊 Keyword Extraction: Provides TF-IDF, frequency analysis, and POS tagging methods
- ⚡ Concurrent Processing: Supports async and batch processing for improved efficiency
- 📦 Structured Results: Uses dataclass to provide type-safe result storage
- 🔄 Backward Compatibility: Provides dictionary format conversion to maintain compatibility

Usage Examples:

1. Basic Usage:
```python
from amnicontext.utils.nltk_utils import process_text_with_nltk

text = "Apple Inc. was founded by Steve Jobs in Cupertino, California."
result = process_text_with_nltk(text)

print(f"Persons: {result.entities.persons}")  # ['Steve Jobs']
print(f"Organizations: {result.entities.organizations}")  # ['Apple Inc.']
print(f"Locations: {result.entities.locations}")  # ['Cupertino', 'California']
print(f"TF-IDF Keywords: {result.keywords.get_top_tfidf(5)}")
```

2. Async Processing:
```python
import asyncio
from amnicontext.utils.nltk_utils import process_text_with_nltk_async

async def process_text():
    result = await process_text_with_nltk_async(text)
    return result

result = asyncio.run(process_text())
```

3. Batch Processing:
```python
from amnicontext.utils.nltk_utils import NLTKProcessor

processor = NLTKProcessor(max_workers=4)
texts = ["Text 1", "Text 2", "Text 3"]

# Batch async processing
results = await processor.process_texts_batch(texts)
```

4. Custom Parameters:
```python
processor = NLTKProcessor(max_workers=4)
result = processor.process_text_sync(
    text,
    max_tfidf_features=50,  # Maximum TF-IDF features
    top_frequency=30,       # Top N frequency analysis
    top_pos=30             # Top N POS analysis
)
```

5. Usage in ExtractArtifactEntityOp:
```python
# Single document processing
result = await extract_op.execute(context, event)

# Batch processing
results = await extract_op.execute_batch(context, events)
```

Dependencies:
- nltk
- scikit-learn
- numpy

Ensure NLTK data packages are installed:
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
    
    Dataclass for storing entity recognition results, containing various types of entities
    
    Example:
    ```python
    result = EntityExtractionResult()
    result.persons = ['Steve Jobs', 'Bill Gates']
    result.organizations = ['Apple Inc.', 'Microsoft']
    result.locations = ['Cupertino', 'Seattle']
    
    # Convert to dictionary format
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
    
    Dataclass for storing keyword extraction results, containing three different keyword extraction methods
    
    Example:
    ```python
    result = KeywordExtractionResult()
    result.tfidf_keywords = [('apple', 0.8), ('technology', 0.6)]
    result.frequency_keywords = [('company', 5), ('product', 3)]
    result.pos_keywords = [('innovation', 2), ('development', 2)]
    
    # Get top N keywords
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
    
    Main NLTK processing result storage class, containing all results from entity recognition and keyword extraction
    
    Example:
    ```python
    processor = NLTKProcessor()
    result = processor.process_text_sync("Apple Inc. was founded by Steve Jobs.")
    
    # Access entity information
    print(result.entities.persons)  # ['Steve Jobs']
    print(result.entities.organizations)  # ['Apple Inc.']
    
    # Access keyword information
    print(result.keywords.get_top_tfidf(5))
    
    # Access metadata
    print(result.metadata['text_length'])
    
    # Convert to dictionary format (backward compatibility)
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
    
    Convenience function for quickly processing text entity recognition and keyword extraction
    
    Args:
        text: Input text to process
        max_tfidf_features: Maximum TF-IDF features to extract
        top_frequency: Number of top frequency keywords
        top_pos: Number of top POS-based keywords
        
    Returns:
        NLTKProcessingResult containing all extracted information
        
    Example:
    ```python
    text = "Apple Inc. was founded by Steve Jobs in Cupertino, California."
    result = process_text_with_nltk(text)
    
    # Access results
    print(f"Persons: {result.entities.persons}")  # ['Steve Jobs']
    print(f"Organizations: {result.entities.organizations}")  # ['Apple Inc.']
    print(f"Locations: {result.entities.locations}")  # ['Cupertino', 'California']
    print(f"TF-IDF Keywords: {result.keywords.get_top_tfidf(5)}")

    # Custom parameters
    result = process_text_with_nltk(text, max_tfidf_features=20, top_frequency=15)
    ```
    """
    processor = NLTKProcessor()
    return processor.process_text_sync(text, max_tfidf_features, top_frequency, top_pos)


async def process_text_with_nltk_async(text: str, max_tfidf_features: int = 50, 
                                      top_frequency: int = 30, top_pos: int = 30) -> NLTKProcessingResult:
    """
    Convenience function for async processing text with NLTK
    
    Async convenience function for asynchronous processing of text entity recognition and keyword extraction
    
    Args:
        text: Input text to process
        max_tfidf_features: Maximum TF-IDF features to extract
        top_frequency: Number of top frequency keywords
        top_pos: Number of top POS-based keywords
        
    Returns:
        NLTKProcessingResult containing all extracted information
        
    Example:
    ```python
    import asyncio
    
    async def process_text():
        text = "Tesla Inc. was founded by Elon Musk in California."
        result = await process_text_with_nltk_async(text)
        
        print(f"Persons: {result.entities.persons}")  # ['Elon Musk']
        print(f"Organizations: {result.entities.organizations}")  # ['Tesla Inc.']
        print(f"Locations: {result.entities.locations}")  # ['California']
        
        return result
    
    # Run async function
    result = asyncio.run(process_text())
    ```
    """
    processor = NLTKProcessor()
    return await processor.process_text_async(text, max_tfidf_features, top_frequency, top_pos)
