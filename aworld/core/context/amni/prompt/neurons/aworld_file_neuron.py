import os
import re
from pathlib import Path
from typing import List, Optional
from ... import ApplicationContext
from . import Neuron
from .neuron_factory import neuron_factory
from aworld.logs.util import logger

AWORLD_FILE_NEURON_NAME = "aworld_file"


@neuron_factory.register(
    name=AWORLD_FILE_NEURON_NAME,
    desc="Neuron for loading AWORLD.md file content",
    prio=50  # Higher priority than basic (100), lower than task (0)
)
class AWORLDFileNeuron(Neuron):
    """
    Neuron that loads and processes AWORLD.md files
    
    Features:
    - Searches for AWORLD.md in multiple locations
    - Supports @import syntax for including other files
    - Caches content to avoid repeated file I/O
    - Handles circular imports gracefully
    """
    
    IMPORT_PATTERN = re.compile(r'^@(.+\.md)\s*$', re.MULTILINE)
    
    def __init__(self):
        super().__init__()
        self._content_cache: Optional[str] = None
        self._last_modified: Optional[float] = None
        self._file_path: Optional[Path] = None
    
    def _find_aworld_file(self, context: ApplicationContext) -> Optional[Path]:
        """
        Find AWORLD.md file in standard locations
        
        Search order (priority):
        1. ~/.aworld/AWORLD.md (user-level, global) - DEFAULT and HIGHEST PRIORITY
        2. .aworld/AWORLD.md (project-specific, if exists)
        3. AWORLD.md (project root, if exists)
        
        Note: User-level config (~/.aworld/AWORLD.md) is the DEFAULT location.
              Project-level configs are OPTIONAL overrides.
        """
        # Get working directory from context
        working_dir = getattr(context, 'working_directory', os.getcwd())
        
        search_paths = [
            Path.home() / '.aworld' / 'AWORLD.md',  # User-level (DEFAULT)
            Path(working_dir) / '.aworld' / 'AWORLD.md',  # Project-specific (optional)
            Path(working_dir) / 'AWORLD.md',  # Project root (optional)
        ]
        
        for path in search_paths:
            if path.exists() and path.is_file():
                logger.info(f"Found AWORLD.md at: {path}")
                return path
        
        logger.debug("No AWORLD.md file found")
        return None
    
    def _resolve_import_path(self, import_path: str, base_path: Path) -> Optional[Path]:
        """
        Resolve import path relative to base file
        
        Args:
            import_path: Path from @import statement
            base_path: Path of file containing the import
            
        Returns:
            Resolved absolute path or None if not found
        """
        import_path = import_path.strip()
        
        # Handle absolute paths
        if import_path.startswith('/'):
            resolved = Path(import_path)
        else:
            # Relative to base file's directory
            resolved = (base_path.parent / import_path).resolve()
        
        if resolved.exists() and resolved.is_file():
            return resolved
        
        logger.warning(f"Import file not found: {import_path} (resolved to {resolved})")
        return None
    
    def _load_file_with_imports(
        self, 
        file_path: Path, 
        visited: Optional[set] = None
    ) -> str:
        """
        Load file content and recursively process @imports
        
        Args:
            file_path: Path to file to load
            visited: Set of already visited files (for circular import detection)
            
        Returns:
            Processed content with all imports resolved
        """
        if visited is None:
            visited = set()
        
        # Circular import detection
        file_path_str = str(file_path.resolve())
        if file_path_str in visited:
            logger.warning(f"Circular import detected: {file_path}")
            return f"\n<!-- Circular import: {file_path} -->\n"
        
        visited.add(file_path_str)
        
        try:
            content = file_path.read_text(encoding='utf-8')
        except Exception as e:
            logger.error(f"Error reading file {file_path}: {e}")
            return f"\n<!-- Error reading file: {file_path} -->\n"
        
        # Process imports
        def replace_import(match):
            import_path = match.group(1)
            resolved_path = self._resolve_import_path(import_path, file_path)
            
            if resolved_path:
                imported_content = self._load_file_with_imports(resolved_path, visited.copy())
                return f"\n<!-- Imported from {import_path} -->\n{imported_content}\n<!-- End import -->\n"
            else:
                return f"\n<!-- Import not found: {import_path} -->\n"
        
        processed_content = self.IMPORT_PATTERN.sub(replace_import, content)
        return processed_content
    
    def _should_reload(self, file_path: Path) -> bool:
        """
        Check if file should be reloaded based on modification time
        """
        if self._content_cache is None:
            return True
        
        if self._file_path != file_path:
            return True
        
        try:
            current_mtime = file_path.stat().st_mtime
            if self._last_modified is None or current_mtime > self._last_modified:
                return True
        except Exception as e:
            logger.warning(f"Error checking file modification time: {e}")
            return True
        
        return False
    
    async def format_items(
        self, 
        context: ApplicationContext, 
        namespace: str = None, 
        **kwargs
    ) -> List[str]:
        """
        Load and format AWORLD.md content
        
        Returns:
            List containing the processed content
        """
        items = []
        
        try:
            # Find AWORLD.md file
            file_path = self._find_aworld_file(context)
            
            if not file_path:
                return items
            
            # Check if reload is needed
            if self._should_reload(file_path):
                logger.info(f"Loading AWORLD.md from: {file_path}")
                
                # Load content with imports
                content = self._load_file_with_imports(file_path)
                
                # Update cache
                self._content_cache = content
                self._file_path = file_path
                self._last_modified = file_path.stat().st_mtime
            
            # Return cached content
            if self._content_cache:
                items.append(self._content_cache)
        
        except Exception as e:
            logger.error(f"Error processing AWORLD.md: {e}")
            items.append(f"<!-- Error loading AWORLD.md: {e} -->")
        
        return items
    
    async def format(
        self, 
        context: ApplicationContext, 
        items: List[str] = None, 
        namespace: str = None, 
        **kwargs
    ) -> str:
        """
        Format AWORLD.md content for injection into system prompt
        """
        if not items:
            items = await self.format_items(context, namespace, **kwargs)
        
        if not items:
            return ""
        
        # Combine all items with proper formatting
        content = "\n\n".join(items)
        
        # Wrap in a clear section
        formatted = f"""
## Project Context (from AWORLD.md)

{content}

---
"""
        return formatted
    
    async def desc(
        self, 
        context: ApplicationContext, 
        namespace: str = None, 
        **kwargs
    ) -> str:
        """
        Return description of this neuron
        """
        return "Project-specific context loaded from AWORLD.md file"
