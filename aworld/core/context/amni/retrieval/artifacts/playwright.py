import json

from pydantic import BaseModel

from aworld.core.context.amni.utils.text_cleaner import truncate_content
from aworld.logs.util import logger
from aworld.output import Artifact, ArtifactType

import re
from typing import List, Optional, Dict, Any

from aworld.output.storage.artifact_repository import CommonEncoder
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum


class ElementType(Enum):
    """Element type enumeration"""
    GENERIC = "generic"
    TABLE = "table"
    ROWGROUP = "rowgroup"
    ROW = "row"
    CELL = "cell"
    HEADING = "heading"
    TEXT = "text"
    LINK = "link"
    BUTTON = "button"
    IMG = "img"
    LIST = "list"
    LISTITEM = "listitem"
    PARAGRAPH = "paragraph"
    UNKNOWN = "unknown"


@dataclass
class YAMLElement:
    """YAML element data structure"""
    element_type: ElementType
    ref: Optional[str] = None
    text: Optional[str] = None
    level: int = 0
    parent_ref: Optional[str] = None
    attributes: Dict[str, Any] = None
    children: List['YAMLElement'] = None
    is_table: bool = False
    is_heading: bool = False
    is_text_content: bool = False
    
    def __post_init__(self):
        if self.children is None:
            self.children = []
        if self.attributes is None:
            self.attributes = {}


class ClickableElement(BaseModel):
    """Data structure for clickable elements"""
    ref: str  # Element reference ID (e.g., e7, e11)
    element_type: str  # Element type (link, button, textbox, img)
    text: Optional[str]  # Element text content
    url: Optional[str]  # Link URL (only for link elements)
    attributes: Dict[str, str]  # Other attributes
    depth: int  # Depth level
    parent_ref: Optional[str]  # Parent element reference

    def format_text(self):
        return f"  <element ref='{self.ref}' element_type='{self.element_type}' url='{truncate_content(self.url, 500)}'>{self.text}<element>"


class TableElement(BaseModel):
    """Data structure for table elements - simplified version, contains only basic information"""
    ref: str  # Element reference ID (e.g., e319)
    element_type: str  # Element type (table, rowgroup, row, cell, text, link, superscript)
    text: Optional[str] = None  # Element text content
    url: Optional[str] = None  # Link URL (only for link elements)
    depth: int = 0  # Depth level
    parent_ref: Optional[str] = None  # Parent element reference
    
    def format_text(self) -> str:
        """Format table element text with simplified display"""
        result = f"  <{self.element_type} ref='{self.ref}'"
        if self.text:
            # Limit text length to avoid excessive context
            display_text = self.text[:100] + "..." if len(self.text) > 100 else self.text
            result += f" text='{display_text}'"
        if self.url:
            result += f" url='{self.url}'"
        result += ">"
        return result


class YAMLClickableParser:
    """YAML format clickable element parser"""

    def __init__(self):
        # Clickable element types
        self.clickable_types = ['link', 'button', 'textbox', 'combobox']

        # Regular expression patterns
        self.element_pattern = re.compile(
            r'^(\s*)- (\w+)(?:\s+"([^"]*)")?\s+\[ref=([^\]]+)\](?:\s+\[cursor=pointer\])?(?:\s*:)?$'
        )
        self.url_pattern = re.compile(r'^\s*- /url:\s*(.+)$')

    def parse_yaml(self, yaml_content: str) -> List[ClickableElement]:
        """Parse YAML content and extract all clickable elements"""
        lines = yaml_content.strip().split('\n')
        elements = []
        current_url = None
        parent_stack = []  # Track parent elements

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Calculate indentation depth
            depth = (len(line) - len(line.lstrip())) // 2

            # Parse element
            match = self.element_pattern.match(line)
            if match:
                indent, element_type, text, ref = match.groups()

                # Check if it's a clickable element
                if self._is_clickable_element(line, element_type):
                    # Get parent element reference
                    parent_ref = parent_stack[depth - 1] if depth > 0 and parent_stack else None

                    element = ClickableElement(
                        ref=ref,
                        element_type=element_type,
                        text=text,
                        url=current_url,
                        attributes={},
                        depth=depth,
                        parent_ref=parent_ref
                    )
                    elements.append(element)

                    # Update parent element stack
                    if depth < len(parent_stack):
                        parent_stack = parent_stack[:depth]
                    parent_stack.append(ref)
                    current_url = None  # Reset URL

            # Parse URL (only for link elements)
            elif line.startswith('- /url:'):
                url_match = self.url_pattern.match(line)
                if url_match:
                    current_url = url_match.group(1).strip()

        return elements

    def _is_clickable_element(self, line: str, element_type: str) -> bool:
        """Determine if an element is clickable"""
        # Check element type
        if element_type in self.clickable_types:
            return True

        # Check if it has cursor=pointer attribute
        if '[cursor=pointer]' in line:
            return True

        return False

    def get_elements_by_type(self, elements: List[ClickableElement], element_type: str) -> List[ClickableElement]:
        """Filter elements by type"""
        return [elem for elem in elements if elem.element_type == element_type]

    def get_elements_with_text(self, elements: List[ClickableElement], text: str) -> List[ClickableElement]:
        """Filter elements by text content"""
        return [elem for elem in elements if elem.text and text.lower() in elem.text.lower()]

    def print_summary(self, elements: List[ClickableElement]):
        """Print summary of clickable elements"""
        print(f"🔍 Found {len(elements)} clickable elements:")
        print("=" * 50)
        self.get_clickable_elements_stats(elements)

    def get_clickable_elements_stats(self, elements: List[ClickableElement]) -> dict:
        """
        Calculate statistics of clickable element types and depth distribution

        Args:
            elements: List of clickable elements

        Returns:
            dict: Dictionary containing statistics by click_type and depth
        """
        from collections import Counter
        type_counter = Counter()
        depth_counter = Counter()

        for elem in elements:
            type_counter[elem.element_type] += 1
            depth_counter[elem.depth] += 1

        stats = {
            "by_type": dict(type_counter),
            "by_depth": dict(depth_counter)
        }
        # Log statistics with emoji
        print(f"📊 Clickable elements stats by type: {stats['by_type']}")
        print(f"🪜 Clickable elements stats by depth: {stats['by_depth']}")
        return stats


class YAMLTableParser:
    """YAML format table element parser - simplified version, extracts only top-level table elements"""

    def __init__(self):
        # Regular expression pattern
        self.table_pattern = re.compile(
            r'^(\s*)- table(?:\s+"([^"]*)")?\s+\[ref=([^\]]+)\](?:\s*:)?$'
        )

    def parse_yaml(self, yaml_content: str) -> List[TableElement]:
        """
        Parse YAML content and extract only top-level table elements
        
        Args:
            yaml_content: YAML format page content
            
        Returns:
            List[TableElement]: List of top-level table elements
            
        Example:
            >>> yaml_content = '''
            ... - table [ref=e319]:
            ...   - rowgroup [ref=e320]:
            ...     - row "Number of mountain peaks" [ref=e321]:
            ... '''
            >>> tables = parser.parse_yaml(yaml_content)
            >>> print(f"Found {len(tables)} tables")
        """
        lines = yaml_content.strip().split('\n')
        elements = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Only match top-level table elements
            match = self.table_pattern.match(line)
            if match:
                indent, text, ref = match.groups()
                
                element = TableElement(
                    ref=ref,
                    element_type='table',
                    text=text,
                    depth=0,
                    parent_ref=None
                )
                elements.append(element)

        return elements

    def print_table_summary(self, elements: List[TableElement]):
        """Print summary of table elements"""
        print(f"📊 Found {len(elements)} tables:")
        print("=" * 50)
        
        for i, table in enumerate(elements):
            print(f"📋 Table {i+1} (ref={table.ref}):")
            if table.text:
                print(f"   - Title: {table.text[:100]}...")
            print()

    def get_table_stats(self, elements: List[TableElement]) -> dict:
        """
        Calculate statistics of table elements
        
        Args:
            elements: List of table elements
            
        Returns:
            dict: Dictionary containing statistics
        """
        stats = {
            "table_count": len(elements),
            "tables": [{"ref": elem.ref, "text": elem.text} for elem in elements]
        }
        
        # Log statistics with emoji
        print(f"📊 Found {len(elements)} tables")
        for i, table in enumerate(elements):
            print(f"📋 Table {i+1}: ref={table.ref}, text={table.text[:50] if table.text else 'No title'}...")
        
        return stats


class PlaywrightHelper:

    @staticmethod
    def extract_clickable_items(yaml_content) -> List[ClickableElement]:
        parser = YAMLClickableParser()

        clickable_elements = parser.parse_yaml(yaml_content)
        # parser.print_summary(clickable_elements)

        return clickable_elements

    @staticmethod
    def extract_table_items(yaml_content: str) -> List[TableElement]:
        """
        Extract table-related elements from YAML content
        
        Args:
            yaml_content: YAML format page content
            
        Returns:
            List[TableElement]: Table element list organized by hierarchical structure
            
        Example:
            >>> yaml_content = '''
            ... - table [ref=e319]:
            ...   - rowgroup [ref=e320]:
            ...     - row "Number of mountain peaks" [ref=e321]:
            ...       - cell "Number of mountain peaks" [ref=e322]:
            ...         - text: Number of mountain peaks
            ... '''
            >>> tables = PlaywrightHelper.extract_table_items(yaml_content)
            >>> print(f"Found {len(tables)} tables")
        """
        parser = YAMLTableParser()
        
        table_elements = parser.parse_yaml(yaml_content)
        # parser.print_table_summary(table_elements)
        
        return table_elements

    @staticmethod
    def extract_page_url(yaml_content):
        lines = yaml_content.strip().split('\n')
        for line in lines:
            if line.startswith("- Page URL:"):
                return line.replace("- Page URL:", "").strip()
        return None

    @staticmethod
    def extract_page_title(yaml_content):
        lines = yaml_content.strip().split('\n')
        for line in lines:
            if line.startswith("- Page Title:"):
                return line.split(":")[1].strip()
        return None

    @staticmethod
    def extract_result(yaml_content: str) -> Optional[Dict[str, Any]]:
        """
        Extract execution result information from YAML content
        
        Args:
            yaml_content: YAML format page snapshot content
            
        Returns:
            Optional[Dict[str, Any]]: Extracted result dictionary, returns None if not found
        """
        lines = yaml_content.strip().split('\n')
        result_start = False
        result_lines = []
        
        for line in lines:
            if line.strip().startswith("### Result"):
                result_start = True
                continue
            elif result_start:
                if line.strip().startswith("### "):
                    break
                if line.strip():
                    result_lines.append(line)
        
        if result_lines:
            try:
                # Try to parse JSON format result
                result_text = '\n'.join(result_lines).strip()
                if result_text.startswith('{') and result_text.endswith('}'):
                    return json.loads(result_text)
                else:
                    # If not JSON format, return raw text
                    return {"raw_result": result_text}
            except json.JSONDecodeError:
                # JSON parsing failed, return raw text
                return {"raw_result": '\n'.join(result_lines).strip()}
        
        return None

    @staticmethod
    def extract_open_tabs(yaml_content: str) -> List[Dict[str, str]]:
        """
        Extract open tab information from YAML content
        
        Args:
            yaml_content: YAML format page snapshot content
            
        Returns:
            List[Dict[str, str]]: Tab information list, each dictionary contains index, title, and url
        """
        lines = yaml_content.strip().split('\n')
        tabs_start = False
        tabs = []
        
        for line in lines:
            if line.strip().startswith("### Open tabs"):
                tabs_start = True
                continue
            elif tabs_start:
                if line.strip().startswith("### "):
                    break
                if line.strip():
                    # Parse tab information, format like: "- 0: (current) [每日行情] (https://www.sge.com.cn/...)"
                    tab_match = re.match(r'-\s*(\d+):\s*(?:\(current\)\s*)?\[([^\]]+)\]\s*\(([^)]+)\)', line.strip())
                    if tab_match:
                        index, title, url = tab_match.groups()
                        tabs.append({
                            "index": index,
                            "title": title,
                            "url": url,
                            "is_current": "(current)" in line
                        })
        
        return tabs

    @staticmethod
    def extract_downloads(yaml_content: str) -> List[Dict[str, str]]:
        """
        Extract download file information from YAML content
        
        Args:
            yaml_content: YAML format page snapshot content
            
        Returns:
            List[Dict[str, str]]: Download file information list, each dictionary contains filename and filepath
            
        Example:
            >>> yaml_content = '''
            ... ### Downloads
            ... - Downloaded file 每日行情数据20250925171446811.xlsx to /tmp/playwright/每日行情数据20250925171446811.xlsx
            ... - Downloaded file 每日行情数据20250925171540475.xlsx to /tmp/playwright/每日行情数据20250925171540475.xlsx
            ... '''
            >>> downloads = PlaywrightHelper.extract_downloads(yaml_content)
            >>> print(f"Found {len(downloads)} downloaded files")
            >>> for download in downloads:
            ...     print(f"File: {download['filename']} -> {download['filepath']}")
        """
        lines = yaml_content.strip().split('\n')
        downloads_start = False
        downloads = []
        
        for line in lines:
            if line.strip().startswith("### Downloads"):
                downloads_start = True
                continue
            elif downloads_start:
                if line.strip().startswith("### "):
                    break
                if line.strip():
                    # Parse download file information, format like: "- Downloaded file filename to /path/to/file"
                    download_match = re.match(r'-\s*Downloaded file\s+(.+?)\s+to\s+(.+)', line.strip())
                    if download_match:
                        filename, filepath = download_match.groups()
                        downloads.append({
                            "filename": filename.strip(),
                            "filepath": filepath.strip()
                        })
        
        return downloads

class PlaywrightSnapshotArtifact(Artifact):

    @staticmethod
    def create(url: str, content: str, clickable_types: list = ['link', 'button', 'textbox', 'combobox'], **kwargs):
        click_elements = PlaywrightHelper.extract_clickable_items(content)
        click_elements = [item.model_dump() for item in click_elements if item.element_type in clickable_types][:1000]
        
        # Extract table elements (only extract top-level tables, limit quantity)
        table_elements = PlaywrightHelper.extract_table_items(content)
        table_elements = [item.model_dump() for item in table_elements][:10]  # Limit table element quantity
        
        page_url = PlaywrightHelper.extract_page_url(content)
        logger.debug(f"[PlaywrightSnapshotArtifact] page_url: {page_url}")
        page_title = PlaywrightHelper.extract_page_title(content)
        logger.debug(f"[PlaywrightSnapshotArtifact] page_title: {page_title}")
        downloads = PlaywrightHelper.extract_downloads(content)
        logger.debug(f"[PlaywrightSnapshotArtifact] downloads: {downloads}")
        result = PlaywrightHelper.extract_result(content)
        logger.debug(f"[PlaywrightSnapshotArtifact] result: {result}")
        open_tabs = PlaywrightHelper.extract_open_tabs(content)

        if result:
            if len(result.get("raw_result")) > 10000:
                result = result.get("raw_result") + (f"... (truncated content is too long {len(result.get('raw_result'))})) \n\n "
                                                     f"if you want to see the full content, please use browser tools to extract detailed content if needed")
            content = result
        if downloads:
            content += f"Downloading Files list\n {downloads}"

        return PlaywrightSnapshotArtifact(
            artifact_type=ArtifactType.PLAYWRIGHT,
            content=result if result else content,
            metadata={
                "url": page_url,
                "title": page_title,
                "click_elements": json.dumps(click_elements, cls=CommonEncoder, ensure_ascii=False),
                "table_elements": json.dumps(table_elements, cls=CommonEncoder, ensure_ascii=False),
                "result": json.dumps(result, cls=CommonEncoder, ensure_ascii=False) if result else None,
                "open_tabs": json.dumps(open_tabs, cls=CommonEncoder, ensure_ascii=False) if open_tabs else None,
                "downloads": json.dumps(downloads, cls=CommonEncoder, ensure_ascii=False) if downloads else None,
            }
        )



    @property
    def clickable_elements(self)-> List[ClickableElement]:
        click_elements_content = self.metadata.get("click_elements")
        click_elements = []
        if click_elements_content:
            click_elements = [ClickableElement.model_validate(item_content) for item_content in json.loads(click_elements_content)]
        return click_elements

    @property
    def table_elements(self) -> List[TableElement]:
        """
        Get table elements from the page
        
        Returns:
            List[TableElement]: Table element list organized by hierarchical structure
        """
        table_elements_content = self.metadata.get("table_elements")
        table_elements = []
        if table_elements_content:
            table_elements = [TableElement.model_validate(item_content) for item_content in json.loads(table_elements_content)]
        return table_elements

    @property
    def page_url(self):
        return self.metadata.get("url")

    @property
    def page_title(self):
        return self.metadata.get("title")

    @property
    def result(self) -> Optional[Dict[str, Any]]:
        """
        Get page execution result
        
        Returns:
            Optional[Dict[str, Any]]: Execution result dictionary, returns None if not found
        """
        result_content = self.metadata.get("result")
        if result_content:
            try:
                return json.loads(result_content)
            except json.JSONDecodeError:
                return None
        return None

    @property
    def open_tabs(self) -> List[Dict[str, str]]:
        """
        Get open tab information
        
        Returns:
            List[Dict[str, str]]: Tab information list
        """
        tabs_content = self.metadata.get("open_tabs")
        if tabs_content:
            try:
                return json.loads(tabs_content)
            except json.JSONDecodeError:
                return []
        return []

    @property
    def downloads(self) -> List[Dict[str, str]]:
        """
        Get download file information
        
        Returns:
            List[Dict[str, str]]: Download file information list, each dictionary contains filename and filepath
        """
        downloads_content = self.metadata.get("downloads")
        if downloads_content:
            try:
                return json.loads(downloads_content)
            except json.JSONDecodeError:
                return []
        return []

    @property
    def summary(self):
        click_elements_context = "\n".join([item.format_text() for item in self.clickable_elements])
        
        # Build table element information (simplified display)
        table_elements_context = ""
        if self.table_elements:
            table_elements_context = "\n".join([item.format_text() for item in self.table_elements])
        
        # Build result information
        result_context = ""
        if self.result:
            result_context = self.result
            if len(self.result) > 10000:
                result_context = f"{self.result[:10000]}... (truncated content is too long {len(self.result)})"
            result_context = f"\n{json.dumps(result_context, ensure_ascii=False, indent=2)}\n"
        
        # Build tab information
        tabs_context = ""
        if self.open_tabs:
            tabs_info = []
            for tab in self.open_tabs:
                current_mark = " (current)" if tab.get("is_current") else ""
                tabs_info.append(f"  - tab#{tab['index']}: {current_mark} [{tab['title']}] ({tab['url']})")
            tabs_context = "\n".join(tabs_info)
        
        # Build download file information
        downloads_context = ""
        if self.downloads:
            downloads_info = []
            for download in self.downloads:
                downloads_info.append(f"  - 📁 {download['filename']} -> {download['filepath']}")
            downloads_context = "\n".join(downloads_info)
        
        return (f"\n<title>{self.page_title}</title>\n"
                f"<url>{self.page_url}</url>\n"
                f"{f'<execution_result>{result_context}</execution_result>' if result_context else ''}"
                f"{f'<open_tabs>{tabs_context}</open_tabs>' if tabs_context else ''}"
                f"\n{f'<downloads>{downloads_context}</downloads>' if downloads_context else ''}"
                f"\n<table_elements description='available tables - use browser tools to extract detailed content if needed'>\n"
                f"{table_elements_context}"
                f"\n</table_elements>\n"
                f"<click_elements description='if you use browser_click tool, please search in click_elements'>\n"
                f"{click_elements_context}"
                f"\n</click_elements>\n")

    def get_clickable_elements_by_type(self, click_type: str):
        def match_type(expected_type, real_type):
            return expected_type == real_type
        return [ elem for elem in self.clickable_elements if match_type(click_type, elem.element_type)]

    def get_tables(self) -> List[TableElement]:
        """
        Get all table elements from the page
        
        Returns:
            List[TableElement]: Table element list
        """
        return self.table_elements

    def get_table_by_ref(self, ref: str) -> Optional[TableElement]:
        """
        Get a specific table element by ref
        
        Args:
            ref: Reference ID of the table element
            
        Returns:
            Optional[TableElement]: Found table element, returns None if not found
        """
        for table in self.table_elements:
            if table.ref == ref:
                return table
        return None



