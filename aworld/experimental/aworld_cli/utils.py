"""
Utility functions for aworld-cli.
"""
import base64
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import urlparse
import httpx


def parse_file_references(text: str) -> Tuple[str, List[str]]:
    """
    Parse @ file references from text and extract file paths or remote URLs.
    
    Supports patterns like:
    - @filename.jpg (local file)
    - @path/to/file.png (local file)
    - @./relative/path/image.gif (local file)
    - @https://example.com/image.jpg (remote URL)
    - @http://example.com/image.png (remote URL)
    
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
        
        >>> text = "Check this @https://example.com/image.png"
        >>> cleaned, urls = parse_file_references(text)
        >>> # cleaned = "Check this"
        >>> # urls = ["data:image/png;base64,..."]
    """
    # Pattern to match @ followed by a file path or URL
    # Matches: @filename, @path/to/file, @./relative/path, @https://...
    pattern = r'@([^\s@]+)'
    
    image_urls: List[str] = []
    cleaned_text = text
    
    # Find all matches
    matches = list(re.finditer(pattern, text))
    
    # Process matches in reverse order to maintain correct indices when removing
    for match in reversed(matches):
        file_ref = match.group(1)
        full_match = match.group(0)  # Includes @ symbol
        
        # Check if it's a remote URL
        is_remote_url = file_ref.startswith(('http://', 'https://'))
        
        if is_remote_url:
            # Handle remote URL
            try:
                image_data, mime_type = _download_remote_image(file_ref)
                if image_data and mime_type:
                    # Encode to base64 data URL
                    base64_data = base64.b64encode(image_data).decode('utf-8')
                    data_url = f"data:{mime_type};base64,{base64_data}"
                    
                    # Add to image URLs list
                    image_urls.insert(0, data_url)  # Insert at beginning to maintain order
                    
                    # Remove the @ reference from text
                    start, end = match.span()
                    cleaned_text = cleaned_text[:start] + cleaned_text[end:]
                else:
                    print(f"⚠️ Failed to download or process remote image: {file_ref}")
            except Exception as e:
                # If download fails, keep the reference in text
                print(f"⚠️ Failed to download remote file {file_ref}: {e}")
        else:
            # Handle local file path
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


def _download_remote_image(url: str) -> Tuple[Optional[bytes], Optional[str]]:
    """
    Download remote image from URL and return image data with MIME type.
    
    Args:
        url: Remote image URL (http:// or https://)
        
    Returns:
        Tuple of (image_data, mime_type) where:
        - image_data: Image binary data, or None if download fails
        - mime_type: MIME type string, or None if download fails
        
    Example:
        >>> data, mime = _download_remote_image("https://example.com/image.jpg")
        >>> # data = b'...' (image bytes)
        >>> # mime = "image/jpeg"
    """
    try:
        # Download with timeout and follow redirects
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            
            # Parse URL once for extension inference
            parsed_url = urlparse(url)
            url_path = parsed_url.path
            
            # Get MIME type from Content-Type header first
            content_type = response.headers.get('Content-Type', '')
            mime_type = None
            
            # Extract MIME type from Content-Type (may include charset)
            if content_type:
                mime_type = content_type.split(';')[0].strip()
                # Validate it's an image MIME type
                if not mime_type.startswith('image/'):
                    mime_type = None
            
            # If no valid MIME type from header, try to infer from URL extension
            if not mime_type:
                if '.' in url_path:
                    extension = '.' + url_path.rsplit('.', 1)[1].lower()
                    mime_type = _get_mime_type(extension)
                else:
                    # Default to jpeg if cannot determine from URL
                    mime_type = 'image/jpeg'
            
            # Validate MIME type is supported
            supported_mimes = {
                'image/jpeg', 'image/jpg', 'image/png', 'image/gif',
                'image/webp', 'image/bmp', 'image/svg+xml'
            }
            if mime_type not in supported_mimes:
                # Try to infer from URL extension as fallback
                if '.' in url_path:
                    extension = '.' + url_path.rsplit('.', 1)[1].lower()
                    mime_type = _get_mime_type(extension)
                else:
                    print(f"⚠️ Unsupported image MIME type: {mime_type}, defaulting to image/jpeg")
                    mime_type = 'image/jpeg'
            
            return response.content, mime_type
            
    except httpx.HTTPError as e:
        print(f"⚠️ HTTP error downloading {url}: {e}")
        return None, None
    except Exception as e:
        print(f"⚠️ Error downloading {url}: {e}")
        return None, None


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


