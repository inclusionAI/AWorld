import json
import time
import traceback
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

import markdown
from marker.converters.pdf import PdfConverter
from marker.models import create_model_dict
from marker.output import text_from_rendered
from marker.settings import settings
from pydantic import BaseModel, Field
from pydantic.fields import FieldInfo

from aworld.logs.util import Color
from examples.nanami.actions.base import ActionArguments, ActionCollection, ActionResponse


class DocumentMetadata(BaseModel):
    """Metadata extracted from document processing."""

    file_name: str = Field(description="Original file name")
    file_size: int = Field(description="File size in bytes")
    file_type: str = Field(description="Document file type/extension")
    page_count: int | None = Field(default=None, description="Number of pages in document")
    processing_time: float = Field(description="Time taken to process the document in seconds")
    extracted_images: list[str] = Field(default_factory=list, description="Paths to extracted image files")
    extracted_media: list[dict[str, str]] = Field(
        default_factory=list, description="list of extracted media files with type and path"
    )
    output_format: str = Field(description="Format of the extracted content")
    llm_enhanced: bool = Field(default=False, description="Whether LLM enhancement was used")
    ocr_applied: bool = Field(default=False, description="Whether OCR was applied")


class DocumentExtractionCollection(ActionCollection):
    """MCP service for comprehensive document content extraction using marker package.

    Supports extraction from PDF, PPTX, DOCX, XLSX, HTML, and EPUB files.
    Provides LLM-friendly text output with structured metadata and media file handling.
    """

    def __init__(self, arguments: ActionArguments) -> None:
        super().__init__(arguments)
        self._models_loaded = False
        self._marker_models = None
        self._media_output_dir = self.workspace / "extracted_media"
        self._media_output_dir.mkdir(exist_ok=True)

        self._color_log("Document Extraction Service initialized", Color.green)
        self._color_log(f"Media output directory: {self._media_output_dir}", Color.blue)

    def _load_marker_models(self) -> None:
        """Load marker models for document processing.

        Lazy loading to avoid unnecessary resource consumption.
        """
        if not self._models_loaded:
            try:
                self._color_log("Loading marker models...", Color.yellow)
                self._marker_models = create_model_dict()
                self._models_loaded = True
                self._color_log("Marker models loaded successfully", Color.green)
            except Exception as e:
                self.logger.error(f"Failed to load marker models: {str(e)}")
                raise

    def _validate_file_path(self, file_path: str) -> Path:
        """Validate and resolve file path.

        Args:
            file_path: Path to the document file

        Returns:
            Resolved Path object

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If file type is not supported
        """
        path = Path(file_path)
        if not path.is_absolute():
            path = self.workspace / path

        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        supported_extensions = {
            ".pdf",
            ".png",
            ".jpg",
            ".jpeg",
            ".tiff",
            ".bmp",
            ".gif",
            ".pptx",
            ".docx",
            ".xlsx",
            ".html",
            ".htm",
            ".epub",
        }

        if path.suffix.lower() not in supported_extensions:
            raise ValueError(
                f"Unsupported file type: {path.suffix}. Supported types: {', '.join(supported_extensions)}"
            )

        return path

    def _extract_content_with_marker(
        self, file_path: Path, page_range: str | None, force_ocr: bool = False
    ) -> dict[str, Any]:
        """Extract content using marker package.

        Args:
            file_path: Path to the document file
            page_range: Specific pages to process (e.g., '0,5-10,20')
            force_ocr: Use OCR to extract text from images if available

        Returns:
            Dictionary containing extracted content and metadata
        """
        start_time = time.time()

        # Prepare marker arguments
        marker_args = {
            "fname": str(file_path),
            "model_lst": self._marker_models,
            "max_pages": None,
            "langs": None,
            "batch_multiplier": 1,
            "force_ocr": force_ocr,
        }

        # Handle page range
        if page_range:
            # Parse page range string (e.g., "0,5-10,20")
            pages = []
            for part in page_range.split(","):
                if "-" in part:
                    start, end = map(int, part.split("-"))
                    pages.extend(range(start, end + 1))
                else:
                    pages.append(int(part))
            marker_args["page_range"] = pages
        converter: PdfConverter = PdfConverter(artifact_dict=self._marker_models)
        rendered = converter(str(file_path))
        text, _, images = text_from_rendered(rendered)
        text = text.encode(settings.OUTPUT_ENCODING, errors="replace").decode(settings.OUTPUT_ENCODING)

        processing_time = time.time() - start_time
        return {
            "content": text,
            "images": images or {},
            "metadata": defaultdict(),
            "processing_time": processing_time,
        }

    def _save_extracted_media(self, images: dict[str, Any], file_stem: str) -> list[dict[str, str]]:
        """Save extracted images and return their paths.

        Args:
            images: Dictionary of extracted images from marker
            file_stem: Base name for saving files

        Returns:
            list of dictionaries containing media type and file paths
        """
        saved_media = []

        for idx, (page_num, image_data) in enumerate(images.items()):
            try:
                # Generate unique filename
                image_filename = f"{file_stem}_page_{page_num}_img_{idx}.png"
                image_path = self._media_output_dir / image_filename

                # Save image data
                if hasattr(image_data, "save"):
                    # PIL Image object
                    image_data.save(image_path)
                elif isinstance(image_data, bytes):
                    # Raw image bytes
                    with open(image_path, "wb") as f:
                        f.write(image_data)
                else:
                    # Handle other formats
                    self.logger.warning(f"Unknown image data type for page {page_num}: {type(image_data)}")
                    continue

                saved_media.append(
                    {"type": "image", "path": str(image_path), "page": str(page_num), "filename": image_filename}
                )

                self._color_log(f"Saved image: {image_filename}", Color.blue)

            except Exception as e:
                self.logger.error(f"Failed to save image from page {page_num}: {str(e)}")

        return saved_media

    def _format_content_for_llm(self, content: str, output_format: str) -> str:
        """Format extracted content to be LLM-friendly.

        Args:
            content: Raw extracted content
            output_format: Desired output format

        Returns:
            Formatted content string
        """
        if output_format.lower() == "markdown":
            # Content is already in markdown format from marker
            return content
        elif output_format.lower() == "json":
            # Structure content as JSON

            return json.dumps({"content": content, "format": "structured_text"}, indent=2)
        elif output_format.lower() == "html":
            # Convert markdown to HTML if needed
            try:
                return markdown.markdown(content)
            except ImportError:
                self.logger.warning("markdown package not available, returning raw content")
                return content
        else:
            return content

    def mcp_extract_document_content(
        self,
        file_path: str = Field(description="Path to the document file to extract content from"),
        output_format: Literal["markdown", "json", "html"] = Field(
            default="markdown", description="Output format: 'markdown', 'json', or 'html'"
        ),
        extract_images: bool = Field(default=True, description="Whether to extract and save images from the document"),
        use_llm: bool = Field(default=False, description="Use LLM for enhanced accuracy (requires additional setup)"),
        page_range: str | None = Field(default=None, description="Specific pages to process (e.g., '0,5-10,20')"),
        force_ocr: bool = Field(default=False, description="Force OCR processing on the entire document"),
        format_lines: bool = Field(
            default=False, description="Reformat lines using local OCR model for better quality"
        ),
    ) -> ActionResponse:
        """Extract content from various document formats using marker package.

        This tool provides comprehensive document content extraction with support for:
        - PDF, PPTX, DOCX, XLSX, HTML, EPUB files
        - Text extraction with proper formatting
        - Image and media extraction
        - Metadata collection
        - LLM-optimized output formatting

        Args:
            args: Document extraction arguments including file path and options

        Returns:
            ActionResponse with extracted content, metadata, and media file paths
        """
        try:
            if isinstance(file_path, FieldInfo):
                file_path = file_path.default
            if isinstance(output_format, FieldInfo):
                output_format = output_format.default
            if isinstance(extract_images, FieldInfo):
                extract_images = extract_images.default
            if isinstance(page_range, FieldInfo):
                page_range = page_range.default
            if isinstance(use_llm, FieldInfo):
                use_llm = use_llm.default
            if isinstance(force_ocr, FieldInfo):
                force_ocr = force_ocr.default
            if isinstance(format_lines, FieldInfo):
                format_lines = format_lines.default

            # Validate input file
            file_path: Path = self._validate_file_path(file_path)
            self._color_log(f"Processing document: {file_path.name}", Color.cyan)

            # Load marker models if needed
            self._load_marker_models()

            # Extract content using marker
            extraction_result = self._extract_content_with_marker(file_path, page_range, force_ocr)

            # Save extracted media if requested
            saved_media = []
            if extract_images and extraction_result["images"]:
                saved_media = self._save_extracted_media(extraction_result["images"], file_path.stem)

            # Format content for LLM consumption
            formatted_content = self._format_content_for_llm(extraction_result["content"], output_format)

            # Prepare metadata
            file_stats = file_path.stat()
            document_metadata = DocumentMetadata(
                file_name=file_path.name,
                file_size=file_stats.st_size,
                file_type=file_path.suffix.lower(),
                page_count=extraction_result["metadata"].get("page_count"),
                processing_time=extraction_result["processing_time"],
                extracted_images=[media["path"] for media in saved_media if media["type"] == "image"],
                extracted_media=saved_media,
                output_format=output_format,
                llm_enhanced=use_llm,
                ocr_applied=force_ocr or format_lines,
            )

            self._color_log(
                f"Successfully extracted content from {file_path.name} "
                f"({len(formatted_content)} characters, {len(saved_media)} media files)",
                Color.green,
            )

            return ActionResponse(success=True, message=formatted_content, metadata=document_metadata.model_dump())

        except FileNotFoundError as e:
            self.logger.error(f"File not found: {str(e)}: {traceback.format_exc()}")
            return ActionResponse(
                success=False, message=f"File not found: {str(e)}", metadata={"error_type": "file_not_found"}
            )
        except ValueError as e:
            self.logger.error(f"Invalid input: {str(e)}: {traceback.format_exc()}")
            return ActionResponse(
                success=False,
                message=f"Invalid input: {str(e)}: {traceback.format_exc()}",
                metadata={"error_type": "invalid_input"},
            )
        except Exception as e:
            self.logger.error(f"Document extraction failed: {str(e)}: {traceback.format_exc()}")
            return ActionResponse(
                success=False,
                message=f"Document extraction failed: {str(e)}",
                metadata={"error_type": "extraction_error"},
            )

    def mcp_list_supported_formats(self) -> ActionResponse:
        """list all supported document formats for extraction.

        Returns:
            ActionResponse with list of supported file formats and their descriptions
        """
        supported_formats = {
            "PDF": "Portable Document Format files (.pdf)",
            "Images": "Image files (.png, .jpg, .jpeg, .tiff, .bmp, .gif)",
            "PowerPoint": "Microsoft PowerPoint presentations (.pptx)",
            "Word": "Microsoft Word documents (.docx)",
            "Excel": "Microsoft Excel spreadsheets (.xlsx)",
            "HTML": "Web pages and HTML documents (.html, .htm)",
            "EPUB": "Electronic publication format (.epub)",
        }

        format_list = "\n".join(
            [f"**{format_name}**: {description}" for format_name, description in supported_formats.items()]
        )

        return ActionResponse(
            success=True,
            message=f"Supported document formats:\n\n{format_list}",
            metadata={"supported_formats": list(supported_formats.keys()), "total_formats": len(supported_formats)},
        )


# Example usage and entry point
if __name__ == "__main__":
    # Default arguments for testing
    args = ActionArguments(
        name="document_extraction_service",
        transport="stdio",
        workspace=None,  # Will use environment variable or home directory
        unittest=True,
    )

    # Initialize and run the document extraction service
    service = DocumentExtractionCollection(args)
    try:
        # resp = service.mcp_extract_document_content(file_path="/Users/arac/Desktop/Qwen3_Technical_Report.pdf")
        resp = service.mcp_extract_document_content(file_path="/Users/arac/Desktop/file-sample_100kB.doc")
        print(resp)
    except Exception as e:
        print(f"An error occurred: {e}: {traceback.format_exc()}")
