from typing import Any, Dict

from ...utils.nltk_utils import NLTKProcessor, NLTKProcessingResult

from ...event import ArtifactEvent
from aworld.logs.util import logger
from .base import BaseOp
from .op_factory import memory_op
from ... import ApplicationContext


@memory_op("extract_artifact_entity")
class ExtractArtifactEntityOp(BaseOp):
    """
    NLTK-based entity and keyword extraction operator
    Uses the NLTKProcessor utility class for better code organization
    Supports concurrent processing and batch operations for better performance
    """

    def __init__(self, name: str = "extract_artifact_entity", max_workers: int = 4, **kwargs):
        """
        Initialize the extract entity operator
        
        Args:
            name: Name of the operator
            max_workers: Maximum number of worker threads for concurrent processing
            **kwargs: Additional arguments
        """
        super().__init__(name, **kwargs)
        self._nltk_processor = NLTKProcessor(max_workers=max_workers)


    async def execute(self, context: ApplicationContext, event: ArtifactEvent = None, **kwargs) -> Dict[str, Any]:
        """
        Extract entities and keywords from artifact using NLTK
        
        Args:
            context: Application context
            event: Artifact event containing the text to process
            **kwargs: Additional arguments (max_tfidf_features, top_frequency, top_pos)
            
        Returns:
            Dictionary containing extracted entities and keywords
        """
        if not event:
            logger.warning("⚠️ ExtractArtifactEntityOp execute failed: event is None")
            return {}

        if not event.artifact:
            logger.warning("⚠️ ExtractArtifactEntityOp execute failed: artifact is None")
            return {}

        artifact = event.artifact
        
        # Extract text content from artifact
        text_content = ""
        if hasattr(artifact, 'content') and artifact.content:
            text_content = str(artifact.content)
        elif hasattr(artifact, 'text') and artifact.text:
            text_content = str(artifact.text)
        elif hasattr(artifact, 'data') and artifact.data:
            text_content = str(artifact.data)
        else:
            logger.warning("⚠️ No text content found in artifact")
            return {}

        if not text_content.strip():
            logger.warning("⚠️ Empty text content in artifact")
            return {}

        try:
            logger.info(f"🔍 Processing text content ({len(text_content)} characters)")
            
            # Get processing parameters from kwargs
            max_tfidf_features = kwargs.get('max_tfidf_features', 50)
            top_frequency = kwargs.get('top_frequency', 30)
            top_pos = kwargs.get('top_pos', 30)
            
            # Process text asynchronously using NLTK processor
            nltk_result: NLTKProcessingResult = await self._nltk_processor.process_text_async(
                text_content, max_tfidf_features, top_frequency, top_pos
            )
            
            # Add timestamp to metadata
            if hasattr(context, 'get_timestamp'):
                nltk_result.metadata['timestamp'] = context.get_timestamp()
            
            # Convert to dictionary format for backward compatibility
            results = nltk_result.to_dict()
            
            logger.info(f"✅ Successfully extracted {len(nltk_result.entities.persons)} persons, "
                           f"{len(nltk_result.entities.organizations)} organizations, "
                           f"{len(nltk_result.entities.locations)} locations, "
                           f"{len(nltk_result.keywords.tfidf_keywords)} TF-IDF keywords")
            
            return results
            
        except Exception as e:
            logger.error(f"❌ Error processing artifact: {e}")
            return {'error': str(e)}

    async def execute_batch(self, context: ApplicationContext, events: list[ArtifactEvent], **kwargs) -> list[Dict[str, Any]]:
        """
        Process multiple artifacts in batch for better performance
        
        Args:
            context: Application context
            events: List of artifact events to process
            **kwargs: Additional arguments (max_tfidf_features, top_frequency, top_pos)
            
        Returns:
            List of dictionaries containing extracted entities and keywords
        """
        if not events:
            logger.warning("⚠️ ExtractArtifactEntityOp execute_batch failed: events list is empty")
            return []

        # Extract text contents from artifacts
        text_contents = []
        valid_events = []
        
        for event in events:
            if not event or not event.artifact:
                continue
                
            artifact = event.artifact
            
            # Extract text content from artifact
            text_content = ""
            if hasattr(artifact, 'content') and artifact.content:
                text_content = str(artifact.content)
            elif hasattr(artifact, 'text') and artifact.text:
                text_content = str(artifact.text)
            elif hasattr(artifact, 'data') and artifact.data:
                text_content = str(artifact.data)
            
            if text_content.strip():
                text_contents.append(text_content)
                valid_events.append(event)

        if not text_contents:
            logger.warning("⚠️ No valid text content found in any artifact")
            return []

        try:
            logger.info(f"🔍 Processing {len(text_contents)} artifacts in batch")
            
            # Get processing parameters from kwargs
            max_tfidf_features = kwargs.get('max_tfidf_features', 50)
            top_frequency = kwargs.get('top_frequency', 30)
            top_pos = kwargs.get('top_pos', 30)
            
            # Process texts in batch using NLTK processor
            nltk_results: list[NLTKProcessingResult] = await self._nltk_processor.process_texts_batch(
                text_contents, max_tfidf_features, top_frequency, top_pos
            )
            
            # Convert results to dictionary format and add metadata
            results = []
            for i, nltk_result in enumerate(nltk_results):
                # Add timestamp to metadata
                if hasattr(context, 'get_timestamp'):
                    nltk_result.metadata['timestamp'] = context.get_timestamp()
                if i < len(valid_events):
                    nltk_result.metadata['artifact_id'] = getattr(valid_events[i].artifact, 'id', None)
                
                results.append(nltk_result.to_dict())
            
            logger.info(f"✅ Successfully processed {len(results)} artifacts in batch")
            return results
            
        except Exception as e:
            logger.error(f"❌ Error processing artifacts in batch: {e}")
            return [{'error': str(e)} for _ in text_contents]

    def __del__(self):
        """Clean up NLTK processor"""
        if hasattr(self, '_nltk_processor'):
            del self._nltk_processor


