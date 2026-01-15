"""
Utility functions for aworld-cli.
"""
import base64
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple


def parse_file_references(text: str) -> Tuple[str, List[str]]:
    """
    Parse @ file references from text and extract file paths.
    
    Supports patterns like:
    - @filename.jpg
    - @path/to/file.png
    - @./relative/path/image.gif
    
    Args:
        text: Input text that may contain @ file references
        
    Returns:
        Tuple of (cleaned_text, image_urls) where:
        - cleaned_text: Text with @ references removed
        - image_urls: List of image data URLs (base64 encoded)
        
    Example:
        >>> text = "Analyze this image @photo.jpg"
        >>> cleaned, urls = parse_file_references(text)
        >>> # cleaned = "Analyze this image"
        >>> # urls = ["data:image/jpeg;base64,..."]
    """
    # Pattern to match @ followed by a file path
    # Matches: @filename, @path/to/file, @./relative/path
    pattern = r'@([^\s@]+)'
    
    image_urls: List[str] = []
    cleaned_text = text
    
    # Find all matches
    matches = list(re.finditer(pattern, text))
    
    # Process matches in reverse order to maintain correct indices when removing
    for match in reversed(matches):
        file_ref = match.group(1)
        full_match = match.group(0)  # Includes @ symbol
        
        # Try to resolve the file path
        file_path = Path(file_ref)
        
        # If not absolute, try relative to current working directory
        if not file_path.is_absolute():
            file_path = Path.cwd() / file_path
        
        # Check if file exists
        if file_path.exists() and file_path.is_file():
            # Check if it's an image file
            image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg'}
            if file_path.suffix.lower() in image_extensions:
                # Read and encode image
                try:
                    with open(file_path, 'rb') as f:
                        image_data = f.read()
                    
                    # Determine MIME type
                    mime_type = _get_mime_type(file_path.suffix.lower())
                    
                    # Encode to base64 data URL
                    base64_data = base64.b64encode(image_data).decode('utf-8')
                    data_url = f"data:{mime_type};base64,{base64_data}"
                    
                    # Add to image URLs list
                    image_urls.insert(0, data_url)  # Insert at beginning to maintain order
                    
                    # Remove the @ reference from text
                    start, end = match.span()
                    cleaned_text = cleaned_text[:start] + cleaned_text[end:]
                    
                except Exception as e:
                    # If file read fails, keep the reference in text
                    print(f"⚠️ Failed to read file {file_path}: {e}")
            else:
                # Not an image file, keep the reference in text
                print(f"⚠️ File {file_path} is not a supported image format")
        else:
            # File doesn't exist, keep the reference in text
            print(f"⚠️ File not found: {file_path}")
    
    # Clean up extra whitespace
    cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()
    
    return cleaned_text, image_urls


def _get_mime_type(extension: str) -> str:
    """
    Get MIME type for image extension.
    
    Args:
        extension: File extension (e.g., '.jpg', '.png')
        
    Returns:
        MIME type string
        
    Example:
        >>> _get_mime_type('.jpg')
        'image/jpeg'
    """
    mime_types = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.webp': 'image/webp',
        '.bmp': 'image/bmp',
        '.svg': 'image/svg+xml'
    }
    return mime_types.get(extension.lower(), 'image/jpeg')


