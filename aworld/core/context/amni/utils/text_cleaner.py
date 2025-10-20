import re
import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


def clean_web_content(content: str, aggressive: bool = False) -> str:
    """
    Clean and normalize web content from internet search results
    
    This function handles common issues found in web content:
    - Removes excessive empty lines and whitespace
    - Normalizes line breaks
    - Removes leading/trailing whitespace
    - Handles common HTML artifacts
    - Optionally removes common web artifacts
    
    Args:
        content (str): Raw web content to clean
        aggressive (bool): Whether to apply aggressive cleaning (removes more artifacts)
        
    Returns:
        str: Cleaned and normalized content
    """
    if not content:
        return ""
    
    try:
        # Remove HTML tags if present (basic cleanup)
        content = re.sub(r'<[^>]+>', '', content)
        
        # Normalize line breaks to \n
        content = re.sub(r'\r\n|\r', '\n', content)
        
        # Remove common web artifacts
        if aggressive:
            # Remove common web navigation elements
            content = re.sub(r'(Home|About|Contact|Privacy|Terms|Login|Sign up|Subscribe|Follow us|Share|Like|Comment)', '', content, flags=re.IGNORECASE)
            # Remove common social media elements
            content = re.sub(r'(@\w+|#\w+|http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+)', '', content)
        
        # Split into lines and process each line
        lines = content.split('\n')
        cleaned_lines = []
        
        for line in lines:
            # Strip whitespace from each line
            cleaned_line = line.strip()
            
            # Only add non-empty lines
            if cleaned_line:
                cleaned_lines.append(cleaned_line)
        
        # Join lines with single newlines, avoiding excessive spacing
        cleaned_content = '\n'.join(cleaned_lines)
        
        # Remove excessive whitespace between words (keep single spaces)
        cleaned_content = re.sub(r' +', ' ', cleaned_content)
        
        # Remove excessive newlines (keep max 2 consecutive newlines)
        cleaned_content = re.sub(r'\n{3,}', '\n\n', cleaned_content)
        
        # Final strip to remove any leading/trailing whitespace
        return cleaned_content.strip()
        
    except Exception as e:
        logger.warning(f"Error cleaning web content: {e}")
        # Return original content if cleaning fails
        return content.strip() if content else ""


def clean_search_results(content: str, remove_duplicates: bool = True) -> str:
    """
    Clean search results content specifically
    
    Args:
        content (str): Search results content to clean
        remove_duplicates (bool): Whether to remove duplicate lines
        
    Returns:
        str: Cleaned search results content
    """
    if not content:
        return ""
    
    try:
        # Apply basic web content cleaning
        cleaned_content = clean_web_content(content, aggressive=True)
        
        if remove_duplicates:
            # Split into lines and remove duplicates while preserving order
            lines = cleaned_content.split('\n')
            seen = set()
            unique_lines = []
            
            for line in lines:
                # Normalize line for comparison (lowercase, strip)
                normalized_line = line.lower().strip()
                if normalized_line and normalized_line not in seen:
                    seen.add(normalized_line)
                    unique_lines.append(line)
            
            cleaned_content = '\n'.join(unique_lines)
        
        return cleaned_content
        
    except Exception as e:
        logger.warning(f"Error cleaning search results: {e}")
        return content.strip() if content else ""


def normalize_text_for_embedding(text: str, max_length: Optional[int] = None) -> str:
    """
    Normalize text for embedding purposes
    
    Args:
        text (str): Text to normalize
        max_length (Optional[int]): Maximum length limit
        
    Returns:
        str: Normalized text suitable for embedding
    """
    if not text:
        return ""
    
    try:
        # Apply basic cleaning
        normalized_text = clean_web_content(text)
        
        # Remove special characters that might interfere with embedding
        normalized_text = re.sub(r'[^\w\s\.\,\!\?\;\:\-\(\)\[\]\{\}]', '', normalized_text)
        
        # Normalize whitespace
        normalized_text = re.sub(r'\s+', ' ', normalized_text)
        
        # Truncate if max_length is specified
        if max_length and len(normalized_text) > max_length:
            normalized_text = normalized_text[:max_length].rsplit(' ', 1)[0] + "..."
        
        return normalized_text.strip()
        
    except Exception as e:
        logger.warning(f"Error normalizing text for embedding: {e}")
        return text.strip() if text else ""


def extract_clean_text_from_html(html_content: str) -> str:
    """
    Extract clean text from HTML content
    
    Args:
        html_content (str): HTML content to extract text from
        
    Returns:
        str: Clean extracted text
    """
    if not html_content:
        return ""
    
    try:
        # Remove script and style elements
        html_content = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
        html_content = re.sub(r'<style[^>]*>.*?</style>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
        
        # Remove HTML tags
        html_content = re.sub(r'<[^>]+>', '', html_content)
        
        # Decode common HTML entities
        html_content = html_content.replace('&nbsp;', ' ')
        html_content = html_content.replace('&amp;', '&')
        html_content = html_content.replace('&lt;', '<')
        html_content = html_content.replace('&gt;', '>')
        html_content = html_content.replace('&quot;', '"')
        html_content = html_content.replace('&#39;', "'")
        
        # Apply web content cleaning
        return clean_web_content(html_content)
        
    except Exception as e:
        logger.warning(f"Error extracting text from HTML: {e}")
        return html_content.strip() if html_content else ""


def clean_and_format_content(content: str, content_type: str = "web") -> str:
    """
    Main function to clean and format content based on type
    
    Args:
        content (str): Content to clean
        content_type (str): Type of content ("web", "search", "document", "html")
        
    Returns:
        str: Cleaned and formatted content
    """
    if not content:
        return ""
    
    try:
        if content_type == "web":
            return clean_web_content(content)
        elif content_type == "search":
            return clean_search_results(content)
        elif content_type == "html":
            return extract_clean_text_from_html(content)
        elif content_type == "document":
            return clean_web_content(content, aggressive=False)
        else:
            # Default to web content cleaning
            return clean_web_content(content)
            
    except Exception as e:
        logger.warning(f"Error cleaning content of type {content_type}: {e}")
        return content.strip() if content else ""

def truncate_content(raw_content, char_limit):
    if raw_content is None:
        raw_content = ''
    if len(raw_content) > char_limit:
        raw_content = raw_content[:char_limit] + "... [truncated]"
    return raw_content
