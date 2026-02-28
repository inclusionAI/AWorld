# coding: utf-8
"""
File parsing hook for aworld-cli executor.

This hook automatically handles @filename file references from user input:
- Text files: Reads content and replaces @filename reference directly (not saved to working_dir)
- Image files: Saves to context working_dir and adds to image_urls

This is a default hook that is automatically registered and enabled.
"""
import base64
import io
import re
from pathlib import Path
from typing import List, Optional, Dict, Any
from urllib.parse import urlparse

from PIL import Image

from aworld.core.context.amni import ApplicationContext
from aworld.core.context.base import Context
from aworld.core.event.base import Message
from aworld.runners.hook.hook_factory import HookFactory
from aworld.logs.util import logger

from .hooks import PostInputParseHook

# Try to get console for output (fallback to None if not available)
try:
    from .._globals import console as global_console
except ImportError:
    global_console = None


@HookFactory.register(name="FileParseHook")
class FileParseHook(PostInputParseHook):
    """
    Default hook for parsing @filename file references from user input.
    
    This hook automatically processes @filename references:
    1. Text files (including .txt, .md, .markdown, etc.): Reads content and replaces @filename reference directly (not saved to working_dir)
    2. Image files (.jpg, .jpeg, .png, .gif, .webp, .bmp, .svg): Saves to context working_dir and adds metadata
    
    This hook is automatically enabled for all LocalAgentExecutor instances.
    It processes files referenced in the format: @filename or @path/to/file
    
    Example:
        User input: "分析这个文档 @document.txt" or "查看这个说明 @readme.md"
        This hook will:
        1. Read file content (text or markdown)
        2. Replace @filename with the file content directly
        3. Result: "分析这个文档 [file content here]" or "查看这个说明 [markdown content here]"
    """
    
    async def exec(self, message: Message, context: Context = None) -> Message:
        """
        Process @filename file references from user input.
        
        Extracts files from user_message (which may contain @filename references),
        processes them (text files merged, images saved), and updates context.
        
        Args:
            message: Message object with user_message and image_urls in headers
            context: ApplicationContext (already created)
            
        Returns:
            Message with updated context and task_content in headers
        """
        # Try to get console from message headers or use global console
        console = message.headers.get('console') if message.headers else None
        if not console and global_console:
            console = global_console
        
        if not context or not isinstance(context, ApplicationContext):
            if console:
                console.print("[dim]🔍 [FileParseHook] Context is not ApplicationContext, skipping file processing[/dim]")
            logger.debug("🔍 [FileParseHook] Context is not ApplicationContext, skipping file processing")
            return message
        
        try:
            # Get user_message from headers
            user_message = message.headers.get('user_message', '')
            if not user_message or not isinstance(user_message, str):
                if console:
                    console.print("[dim]🔍 [FileParseHook] No user_message found, skipping[/dim]")
                logger.debug("🔍 [FileParseHook] No user_message found, skipping")
                return message
            
            # Get image_urls from headers (may be None or empty list)
            image_urls = message.headers.get('image_urls', []) or []
            if not isinstance(image_urls, list):
                image_urls = []
            
            # Parse @filename references
            # Improved pattern: exclude patterns that are clearly not file references
            # Exclude: ending with ), ], }, or starting with definition. (code definition markers)
            pattern = r'@([^\s@]+)'
            matches = list(re.finditer(pattern, user_message))
            
            if not matches:
                if console:
                    console.print("[dim]🔍 [FileParseHook] No @filename references found[/dim]")
                logger.debug("🔍 [FileParseHook] No @filename references found")
                return message
            
            # Filter out matches that are clearly not file references
            valid_matches = []
            for match in matches:
                file_ref = match.group(1)
                # Skip patterns that are clearly not file references:
                # - Ending with closing brackets/parentheses: name), value], etc.
                # - Starting with "definition." (code definition markers)
                # - Single character references (likely code symbols)
                if (file_ref.endswith((')', ']', '}')) or 
                    file_ref.startswith('definition.') or
                    len(file_ref) <= 1):
                    continue
                valid_matches.append(match)
            
            if not valid_matches:
                if console:
                    console.print("[dim]🔍 [FileParseHook] No valid @filename references found[/dim]")
                logger.debug("🔍 [FileParseHook] No valid @filename references found")
                return message
            
            if console:
                console.print(f"[dim]📁 [FileParseHook] Processing {len(valid_matches)} file reference(s)[/dim]")
            logger.info(f"📁 [FileParseHook] Processing {len(valid_matches)} file reference(s)")
            
            # Store text file replacements: (start_pos, end_pos, content, filename)
            # Store image removals: (start_pos, end_pos) - images are removed, not replaced
            text_file_replacements: List[tuple] = []
            image_removals: List[tuple] = []
            cleaned_text = user_message
            
            # First pass: collect all file information (images and text files)
            for match in valid_matches:
                file_ref = match.group(1)
                start, end = match.span()
                
                # Check if it's a remote URL
                is_remote_url = file_ref.startswith(('http://', 'https://'))
                
                if is_remote_url:
                    # Handle remote URL (for images)
                    try:
                        image_data, mime_type = await self._download_remote_image(file_ref)
                        if image_data and mime_type:
                            # Encode to base64 data URL
                            base64_data = base64.b64encode(image_data).decode('utf-8')
                            data_url = f"data:{mime_type};base64,{base64_data}"
                            
                            # Add to image URLs list
                            image_urls.insert(0, data_url)
                            
                            # Store removal info (images are removed, not replaced)
                            image_removals.append((start, end))
                            
                            if console:
                                console.print(f"[dim]📷 [FileParseHook] Downloaded remote image: {file_ref}[/dim]")
                            logger.info(f"📷 [FileParseHook] Downloaded remote image: {file_ref}")
                    except Exception as e:
                        if console:
                            console.print(f"[yellow]⚠️ [FileParseHook] Failed to download remote file {file_ref}: {e}[/yellow]")
                        logger.warning(f"⚠️ [FileParseHook] Failed to download remote file {file_ref}: {e}")
                else:
                    # Handle local file path
                    file_path = Path(file_ref)
                    
                    # If not absolute, try relative to current working directory
                    if not file_path.is_absolute():
                        file_path = Path.cwd() / file_path
                    
                    # Resolve the path to handle any symlinks or relative components
                    try:
                        file_path = file_path.resolve()
                    except Exception:
                        pass  # If resolve fails, use original path
                    
                    # Check if file exists
                    if file_path.exists() and file_path.is_file():
                        # Check if it's an image file
                        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg'}
                        if file_path.suffix.lower() in image_extensions:
                            # Read and process image
                            try:
                                with open(file_path, 'rb') as f:
                                    image_data = f.read()
                                
                                # Get image dimensions
                                try:
                                    image = Image.open(io.BytesIO(image_data))
                                    width, height = image.size
                                except Exception:
                                    width, height = 0, 0
                                
                                # Save image to context working_dir
                                image_format = file_path.suffix.lower().lstrip('.')
                                if width > 0 and height > 0:
                                    filename = f"image_{width}x{height}.{image_format}"
                                else:
                                    filename = f"input_image_{len(image_urls) + 1}.{image_format}"
                                
                                image_path_in_context = f"images/{filename}"
                                await context.add_file(image_path_in_context, image_data)
                                
                                # Determine MIME type
                                mime_type = self._get_mime_type(file_path.suffix.lower())
                                
                                # Encode to base64 data URL
                                base64_data = base64.b64encode(image_data).decode('utf-8')
                                data_url = f"data:{mime_type};base64,{base64_data}"
                                
                                # Add to image URLs list
                                image_urls.insert(0, data_url)
                                
                                # Store removal info (images are removed, not replaced)
                                image_removals.append((start, end))
                                
                                if console:
                                    console.print(f"[dim]📷 [FileParseHook] Saved image to {image_path_in_context} ({width}x{height})[/dim]")
                                logger.info(f"📷 [FileParseHook] Saved image to {image_path_in_context} ({width}x{height})")
                            except Exception as e:
                                if console:
                                    console.print(f"[yellow]⚠️ [FileParseHook] Failed to read image file {file_path}: {e}[/yellow]")
                                logger.warning(f"⚠️ [FileParseHook] Failed to read image file {file_path}: {e}")
                        else:
                            # Try to read as text file (including .txt, .md, .markdown, etc.)
                            # Text files are not saved to working_dir, content directly replaces @filename
                            try:
                                # Try UTF-8 first
                                try:
                                    with open(file_path, 'r', encoding='utf-8') as f:
                                        file_content = f.read()
                                except UnicodeDecodeError:
                                    # Try other common encodings
                                    for encoding in ['gbk', 'gb2312', 'latin-1']:
                                        try:
                                            with open(file_path, 'r', encoding=encoding) as f:
                                                file_content = f.read()
                                            break
                                        except UnicodeDecodeError:
                                            continue
                                    else:
                                        if console:
                                            console.print(f"[yellow]⚠️ [FileParseHook] Failed to decode text file {file_path} with common encodings[/yellow]")
                                        logger.warning(f"⚠️ [FileParseHook] Failed to decode text file {file_path} with common encodings")
                                        continue
                                
                                # Store replacement info: (start_pos, end_pos, content, filename)
                                file_name = file_path.name
                                text_file_replacements.append((start, end, file_content, file_name))
                                
                                if console:
                                    console.print(f"[dim]📄 [FileParseHook] Read text file: {file_path} ({len(file_content)} characters)[/dim]")
                                logger.info(f"📄 [FileParseHook] Read text file: {file_path} ({len(file_content)} characters)")
                            except Exception as e:
                                if console:
                                    console.print(f"[yellow]⚠️ [FileParseHook] Failed to read text file {file_path}: {e}[/yellow]")
                                logger.warning(f"⚠️ [FileParseHook] Failed to read text file {file_path}: {e}")
                    else:
                        # Only warn if the reference looks like a valid file path
                        # (has extension or contains path separators)
                        looks_like_file = (
                            '.' in file_ref and len(file_ref.split('.')[-1]) <= 5  # Has extension
                            or '/' in file_ref or '\\' in file_ref  # Has path separators
                            or file_ref.startswith(('./', '../'))  # Relative path
                        )
                        if looks_like_file:
                            if console:
                                console.print(f"[yellow]⚠️ [FileParseHook] File not found: {file_path} (resolved from: {file_ref})[/yellow]")
                            logger.warning(f"⚠️ [FileParseHook] File not found: {file_path} (resolved from: {file_ref})")
                        else:
                            # Silently skip - likely not a file reference
                            logger.debug(f"🔍 [FileParseHook] Skipping non-file reference: {file_ref}")
            
            # Second pass: Apply all replacements and removals in reverse order
            # Combine all operations and sort by start position in reverse order
            all_operations: List[tuple] = []
            
            # Add text file replacements (type='replace')
            for start, end, content, filename in text_file_replacements:
                all_operations.append(('replace', start, end, content))
                if console:
                    console.print(f"[dim]🔧 [FileParseHook] Will replace @{filename} at position {start}-{end} with {len(content)} characters[/dim]")
                logger.debug(f"🔧 [FileParseHook] Will replace @{filename} at position {start}-{end} with {len(content)} characters")
            
            # Add image removals (type='remove')
            for start, end in image_removals:
                all_operations.append(('remove', start, end, None))
            
            # Sort by start position in reverse order (process from end to start)
            all_operations.sort(key=lambda x: x[1], reverse=True)
            
            # Apply all operations
            for op_type, start, end, content in all_operations:
                if op_type == 'replace':
                    # Replace @filename with file content directly
                    before_len = len(cleaned_text)
                    cleaned_text = cleaned_text[:start] + content + cleaned_text[end:]
                    after_len = len(cleaned_text)
                    if console:
                        console.print(f"[dim]✅ [FileParseHook] Replaced text at {start}-{end}: {before_len} -> {after_len} chars[/dim]")
                    logger.debug(f"✅ [FileParseHook] Replaced text at {start}-{end}: {before_len} -> {after_len} chars")
                elif op_type == 'remove':
                    # Remove @filename reference (for images)
                    cleaned_text = cleaned_text[:start] + cleaned_text[end:]
            
            final_text = cleaned_text.strip()
            
            if console and text_file_replacements:
                console.print(f"[dim]📝 [FileParseHook] Final text length: {len(final_text)} characters (original: {len(user_message)})[/dim]")
            logger.debug(f"📝 [FileParseHook] Final text length: {len(final_text)} characters (original: {len(user_message)})")
            
            # Update message headers with processed content
            message.headers = message.headers or {}
            message.headers['user_message'] = final_text
            message.headers['task_content'] = final_text
            message.headers['image_urls'] = image_urls
            message.headers['context'] = context
            
            # Update task_input.task_content directly if task_input is available
            # This allows the hook to directly modify the task_input object
            task_input = message.headers.get('task_input')
            if task_input and hasattr(task_input, 'task_content'):
                task_input.task_content = final_text
                message.headers['task_input'] = task_input
            
            if text_file_replacements or image_urls:
                if console:
                    console.print(f"[dim]✅ [FileParseHook] Processed files: {len(text_file_replacements)} text file(s), {len(image_urls)} image(s)[/dim]")
                logger.info(f"✅ [FileParseHook] Processed files: {len(text_file_replacements)} text file(s), {len(image_urls)} image(s)")
            
            return message
            
        except Exception as e:
            if console:
                console.print(f"[red]❌ [FileParseHook] Error processing files: {e}[/red]")
            logger.error(f"❌ [FileParseHook] Error processing files: {e}", exc_info=True)
            return message
    
    async def _download_remote_image(self, url: str) -> tuple[Optional[bytes], Optional[str]]:
        """
        Download remote image from URL and return image data with MIME type.
        
        Args:
            url: Remote image URL (http:// or https://)
            
        Returns:
            Tuple of (image_data, mime_type) where:
            - image_data: Image binary data, or None if download fails
            - mime_type: MIME type string, or None if download fails
        """
        try:
            import httpx
            
            # Download with timeout and follow redirects
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(url)
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
                        mime_type = self._get_mime_type(extension)
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
                        mime_type = self._get_mime_type(extension)
                    else:
                        logger.warning(f"⚠️ [FileParseHook] Unsupported image MIME type: {mime_type}, defaulting to image/jpeg")
                        mime_type = 'image/jpeg'
                
                return response.content, mime_type
                
        except Exception as e:
            logger.warning(f"⚠️ [FileParseHook] Error downloading {url}: {e}")
            return None, None
    
    def _get_mime_type(self, extension: str) -> str:
        """
        Get MIME type for image extension.
        
        Args:
            extension: File extension (e.g., '.jpg', '.png')
            
        Returns:
            MIME type string
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
