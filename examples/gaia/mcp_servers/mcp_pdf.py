import logging
import os
import re
import sys
import traceback
from typing import Any, Dict, List, Optional, Set, Union

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from aworld.logs.util import logger

# PDF processing libraries
try:
    import PyPDF2
except ImportError:
    logging.warning("PyPDF2 library is not installed. Some PDF operations might be limited.")
    PyPDF2 = None  # Explicitly set to None for later checks

try:
    import fitz  # PyMuPDF
except ImportError:
    logging.error("PyMuPDF (fitz) library is not installed. Text extraction and image extraction will not work.")
    fitz = None  # Explicitly set to None


# pylint: disable=W0707
# Initialize MCP server
mcp = FastMCP("pdf-server")


@mcp.tool(
    description="Extracts text content from a PDF file. Supports pagination, character limits, and page-by-page output."
)
async def extract_pdf_text(
    file_path: str = Field(description="The absolute path to the PDF file."),
    page_numbers: Optional[List[int]] = Field(
        default=None,
        description="Optional. A list of page numbers (0-indexed) to extract text from. "
        "If not provided, extracts from all pages.",
    ),
    max_chars: Optional[int] = Field(
        default=None,
        description="Optional. Maximum number of characters to return. "
        "Text will be truncated if it exceeds this limit.",
    ),
    return_format: str = Field(
        default="full_text",
        description="Optional. 'full_text' (default) to return a single string, "
        "or 'by_page' to return a dictionary of page_number:text.",
        enum=["full_text", "by_page"],
    ),
) -> Union[str, Dict[str, str]]:
    """
    Extracts text content from a PDF file.

    Args:
        file_path: The absolute path to the PDF file.
        page_numbers: Optional. A list of page numbers (0-indexed) to extract text from.
                      If not provided, extracts from all pages.
        max_chars: Optional. Maximum number of characters to return. Text will be truncated
                   if it exceeds this limit.
        return_format: Optional. 'full_text' (default) to return a single string,
                       or 'by_page' to return a dictionary of page_number:text.

    Returns:
        A string containing the full text or a dictionary mapping page numbers (1-indexed)
        to text content, depending on `return_format`.

    Raises:
        RuntimeError: If PyMuPDF (fitz) is not installed.
        FileNotFoundError: If the specified file_path does not exist.
        RuntimeError: If an error occurs during processing with both fitz and PyPDF2.
    """
    if fitz is None:
        raise RuntimeError("PyMuPDF (fitz) library is required for text extraction but not found.")
    if not os.path.exists(file_path):
        logger.error(f"File not found: {file_path}")
        raise FileNotFoundError(f"File not found: {file_path}")

    try:
        doc = fitz.open(file_path)
        current_char_count = 0

        pages_to_process = []
        if page_numbers:
            for pn in page_numbers:
                if 0 <= pn < len(doc):
                    pages_to_process.append(pn)
                else:
                    logger.warning(f"Page number {pn} is out of range for PDF with {len(doc)} pages. Skipping.")
        else:
            pages_to_process = range(len(doc))

        if return_format == "by_page":
            page_text_map = {}
            for page_num in pages_to_process:
                page = doc.load_page(page_num)
                text = page.get_text("text") or ""
                if max_chars is not None:
                    remaining_chars_for_page = max_chars - current_char_count
                    if remaining_chars_for_page <= 0 and len(pages_to_process) > 1:
                        logger.warning(
                            f"Max characters ({max_chars}) reached. Skipping further pages for 'by_page' format."
                        )
                        break
                    text = text[:remaining_chars_for_page]
                    current_char_count += len(text)
                page_text_map[f"page_{page_num + 1}"] = text
                if max_chars is not None and current_char_count >= max_chars and len(pages_to_process) > 1:
                    break
            doc.close()
            return page_text_map
        else:  # full_text
            full_text_content = ""
            for page_num in pages_to_process:
                page = doc.load_page(page_num)
                text = page.get_text("text") or ""

                if max_chars is not None:
                    chars_to_add = len(text)
                    if current_char_count + chars_to_add > max_chars:
                        chars_to_add = max_chars - current_char_count
                        text = text[:chars_to_add]

                    full_text_content += text
                    current_char_count += chars_to_add
                    if current_char_count >= max_chars:
                        break
                else:
                    full_text_content += text
            doc.close()
            return full_text_content

    except Exception as e:
        logger.error(f"Error extracting text from {file_path} using fitz: {e}\n{traceback.format_exc()}")
        if "doc" in locals() and doc:  # type: ignore
            doc.close()  # type: ignore
        # Fallback to PyPDF2 if fitz fails
        if PyPDF2 is not None:
            logger.info(f"Attempting fallback to PyPDF2 for text extraction from {file_path}")
            try:
                text_content_pypdf = ""
                with open(file_path, "rb") as pdf_file:
                    reader = PyPDF2.PdfReader(pdf_file)
                    pages_to_process_pypdf = []
                    if page_numbers:
                        pages_to_process_pypdf = [pn for pn in page_numbers if 0 <= pn < len(reader.pages)]
                    else:
                        pages_to_process_pypdf = range(len(reader.pages))

                    if return_format == "by_page":
                        page_text_map_pypdf = {}
                        current_char_count_pypdf = 0
                        for page_num in pages_to_process_pypdf:
                            page_obj = reader.pages[page_num]
                            text = page_obj.extract_text() or ""
                            if max_chars is not None:
                                remaining_chars_for_page = max_chars - current_char_count_pypdf
                                if remaining_chars_for_page <= 0 and len(pages_to_process_pypdf) > 1:
                                    break
                                text = text[:remaining_chars_for_page]
                                current_char_count_pypdf += len(text)
                            page_text_map_pypdf[f"page_{page_num + 1}"] = text
                            if (
                                max_chars is not None
                                and current_char_count_pypdf >= max_chars
                                and len(pages_to_process_pypdf) > 1
                            ):
                                break
                        return page_text_map_pypdf
                    else:
                        for page_num in pages_to_process_pypdf:
                            page_obj = reader.pages[page_num]
                            text_content_pypdf += page_obj.extract_text() or ""
                            if max_chars is not None and len(text_content_pypdf) >= max_chars:
                                text_content_pypdf = text_content_pypdf[:max_chars]
                                break
                        return text_content_pypdf
            except Exception as pypdf_e:
                logger.error(f"Error during PyPDF2 fallback for {file_path}: {pypdf_e}\n{traceback.format_exc()}")
                raise RuntimeError(f"Error processing PDF for text extraction (fitz and PyPDF2 failed): {str(pypdf_e)}")
        else:
            raise RuntimeError(f"Error processing PDF for text extraction with fitz: {str(e)}")


@mcp.tool(description="Retrieves metadata from a PDF file (e.g., author, title, number of pages).")
async def get_pdf_metadata(file_path: str = Field(description="The absolute path to the PDF file.")) -> Dict[str, Any]:
    """
    Retrieves metadata from a PDF file.

    Args:
        file_path: The absolute path to the PDF file.

    Returns:
        A dictionary containing metadata such as author, title, subject, creationDate,
        modDate, and number of pages. Returns None for fields if not found.

    Raises:
        FileNotFoundError: If the specified file_path does not exist.
        RuntimeError: If no PDF library is available or an error occurs during processing.
    """
    if not os.path.exists(file_path):
        logger.error(f"File not found: {file_path}")
        raise FileNotFoundError(f"File not found: {file_path}")
    try:
        if fitz is not None:
            doc = fitz.open(file_path)
            metadata = doc.metadata  # pylint: disable=no-member
            num_pages = len(doc)
            doc.close()
            return {
                "author": metadata.get("author"),
                "creator": metadata.get("creator"),
                "producer": metadata.get("producer"),
                "subject": metadata.get("subject"),
                "title": metadata.get("title"),
                "creationDate": metadata.get("creationDate"),
                "modDate": metadata.get("modDate"),
                "num_pages": num_pages,
            }
        elif PyPDF2 is not None:
            with open(file_path, "rb") as pdf_file:
                reader = PyPDF2.PdfReader(pdf_file)
                metadata_pypdf = reader.metadata
                return {
                    "author": metadata_pypdf.author if metadata_pypdf else None,
                    "creator": metadata_pypdf.creator if metadata_pypdf else None,
                    "producer": metadata_pypdf.producer if metadata_pypdf else None,
                    "subject": metadata_pypdf.subject if metadata_pypdf else None,
                    "title": metadata_pypdf.title if metadata_pypdf else None,
                    "num_pages": len(reader.pages),
                }
        else:
            raise RuntimeError("No PDF library available to get metadata.")
    except Exception as e:
        logger.error(f"Error getting metadata from {file_path}: {e}\n{traceback.format_exc()}")
        raise RuntimeError(f"Error processing PDF for metadata: {str(e)}")


@mcp.tool(
    description=(
        "Count the number of specific text options that appear in a PDF file "
        "and returns the exact occurence of each text option."
    )
)
async def count_text_occurrences(
    file_path: str = Field(description="The absolute path to the PDF file."),
    text_options: Set[str] = Field(description="A set of text strings to search for in the PDF."),
) -> Dict[str, int]:
    """
    Count the number of specific text options that appear in a PDF file
    and returns the exact occurence of each text option.

    Args:
        file_path: The absolute path to the PDF file.
        text_options: List of text strings to search for.

    Returns:
        A dictionary counts each text option found in the given PDF.
    """
    if not os.path.exists(file_path):
        logger.error(f"File not found: {file_path}")
        raise FileNotFoundError(f"File not found: {file_path}")
    results = {option: 0 for option in text_options}
    try:
        doc = fitz.open(file_path)
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            page_text = page.get_text("text") or ""
            for option in text_options:
                matches = re.findall(option, page_text, re.IGNORECASE)
                results[option] += len(matches)
        return results
    except Exception as e:
        logger.error(f"Error searching text in PDF {file_path}: {e}\n{traceback.format_exc()}")
        raise RuntimeError(f"Error searching text in PDF: {str(e)}")
    finally:
        if doc:
            doc.close()


@mcp.tool(
    description="Splits a PDF file into multiple PDF files based on page ranges. "
    "If no ranges are provided, each page becomes a separate file."
)
async def split_pdf(
    file_path: str = Field(description="Absolute path to the input PDF file."),
    output_directory: str = Field(description="Directory to save the split PDF files."),
    ranges: Optional[List[List[int]]] = Field(
        default=None,
        description="Optional. A list of page ranges, e.g., [[0,0], [1,2]] for page 1 and pages 2-3 (0-indexed).",
    ),
) -> List[str]:
    """
    Splits a PDF file into multiple PDF files based on specified page ranges.

    Args:
        file_path: Absolute path to the input PDF file.
        output_directory: Directory to save the split PDF files.
        ranges: Optional. A list of page ranges (0-indexed). Each inner list
                [start_page, end_page] defines a range. If None, each page
                is saved as a separate file.

    Returns:
        A list of absolute paths to the generated split PDF files.

    Raises:
        RuntimeError: If PyPDF2 is not installed.
        FileNotFoundError: If the input file does not exist.
        ValueError: If the output directory cannot be created.
        RuntimeError: If an error occurs during the splitting process.
    """
    if PyPDF2 is None:
        raise RuntimeError("PyPDF2 library is required for split_pdf but not found.")
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Input file not found: {file_path}")
    if not os.path.isdir(output_directory):
        try:
            os.makedirs(output_directory, exist_ok=True)
        except Exception as e:
            raise ValueError(f"Cannot create output directory: {output_directory}, error: {str(e)}")

    output_files = []
    try:
        reader = PyPDF2.PdfReader(file_path)
        total_pages = len(reader.pages)

        if not ranges:  # If no ranges are provided, split each page into a separate file
            ranges = [[i, i] for i in range(total_pages)]

        for i, page_range in enumerate(ranges):
            writer = PyPDF2.PdfWriter()
            start_page = page_range[0]
            end_page = page_range[1] if len(page_range) > 1 else start_page

            if not (0 <= start_page < total_pages and 0 <= end_page < total_pages and start_page <= end_page):
                logger.warning(f"Invalid page range: {page_range}. Skipping.")
                continue

            for page_num in range(start_page, end_page + 1):
                writer.add_page(reader.pages[page_num])

            base_name = os.path.splitext(os.path.basename(file_path))[0]
            output_filename = os.path.join(
                output_directory,
                (
                    f"{base_name}_split_{i + 1}_pages_{start_page + 1}-"
                    f"{(end_page if end_page >= start_page else start_page) + 1}.pdf"
                ),
            )

            with open(output_filename, "wb") as output_pdf:
                writer.write(output_pdf)
            output_files.append(output_filename)
        return output_files
    except Exception as e:
        logger.error(f"Error splitting PDF {file_path}: {e}\n{traceback.format_exc()}")
        raise RuntimeError(f"Error splitting PDF: {str(e)}")


@mcp.tool(description="Merges multiple PDF files into a single PDF file.")
async def merge_pdfs(
    input_file_paths: List[str] = Field(description="List of absolute paths to input PDF files."),
    output_file_path: str = Field(description="Absolute path for the merged output PDF file."),
) -> str:
    """
    Merges multiple PDF files into a single output PDF file.

    Args:
        input_file_paths: List of absolute paths to the input PDF files.
        output_file_path: Absolute path for the merged output PDF file.

    Returns:
        The absolute path to the generated merged PDF file.

    Raises:
        RuntimeError: If PyPDF2 is not installed.
        FileNotFoundError: If any of the input files do not exist.
        RuntimeError: If an error occurs during the merging process.
    """
    if PyPDF2 is None:
        raise RuntimeError("PyPDF2 library is required for merge_pdfs but not found.")
    merger = PyPDF2.PdfMerger()
    try:
        for pdf_path in input_file_paths:
            if not os.path.exists(pdf_path):
                raise FileNotFoundError(f"Input file not found: {pdf_path}")
            merger.append(pdf_path)

        output_dir = os.path.dirname(output_file_path)
        if output_dir and not os.path.isdir(output_dir):
            os.makedirs(output_dir, exist_ok=True)

        merger.write(output_file_path)
        merger.close()
        return output_file_path
    except Exception as e:
        logger.error(f"Error merging PDFs: {e}\n{traceback.format_exc()}")
        if hasattr(merger, "close"):
            merger.close()
        raise RuntimeError(f"Error merging PDFs: {str(e)}")


@mcp.tool(description="Extracts all images from a PDF file and saves them to a specified directory (uses PyMuPDF).")
async def extract_images_from_pdf(
    file_path: str = Field(description="Absolute path to the input PDF file."),
    output_directory: str = Field(description="Directory to save the extracted images."),
) -> List[str]:
    """
    Extracts all images from a PDF file and saves them to a specified directory.

    Args:
        file_path: Absolute path to the input PDF file.
        output_directory: Directory to save the extracted images.

    Returns:
        A list of absolute paths to the extracted image files.

    Raises:
        RuntimeError: If PyMuPDF (fitz) is not installed.
        FileNotFoundError: If the input file does not exist.
        ValueError: If the output directory cannot be created.
        RuntimeError: If an error occurs during the image extraction process.
    """
    if fitz is None:
        raise RuntimeError("PyMuPDF (fitz) library is required for image extraction but not found.")
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Input file not found: {file_path}")
    if not os.path.isdir(output_directory):
        try:
            os.makedirs(output_directory, exist_ok=True)
        except Exception as e:
            raise ValueError(f"Cannot create output directory: {output_directory}, error: {str(e)}")

    extracted_image_paths = []
    doc = None  # Initialize doc to None
    try:
        doc = fitz.open(file_path)
        for page_index in range(len(doc)):
            page = doc.load_page(page_index)
            image_list = page.get_images(full=True)
            for img_index, img_info in enumerate(image_list):
                xref = img_info[0]
                base_image = doc.extract_image(xref)
                image_bytes = base_image["image"]
                image_ext = base_image["ext"]
                image_filename = os.path.join(output_directory, f"page{page_index + 1}_img{img_index + 1}.{image_ext}")
                with open(image_filename, "wb") as img_file:
                    img_file.write(image_bytes)
                extracted_image_paths.append(image_filename)
        doc.close()
        return extracted_image_paths
    except Exception as e:
        logger.error(f"Error extracting images from {file_path}: {e}\n{traceback.format_exc()}")
        if doc:
            doc.close()
        raise RuntimeError(f"Error extracting images: {str(e)}")


def main():  # Changed to synchronous function
    """
    Main function to start the MCP server.
    """
    load_dotenv()  # Load environment variables
    # pdf_manager = PDFToolManager() # Removed
    # tools = [...] # Removed, tools are registered via decorator
    # server = StdioServer(tools=tools) # Removed
    # await server.run_forever() # Removed
    mcp.run(transport="stdio")  # Use FastMCP's run method


# Make the module callable for uvx (Reference audio_server.py)
def __call__():
    """
    Make the module callable for uvx.
    This function is called when the module is executed directly.
    """
    main()


# Add this for compatibility with uvx
if __name__ != "__main__":  # Ensure this is also set when imported as a module
    sys.modules[__name__].__call__ = __call__

# Run the server when the script is executed directly
if __name__ == "__main__":
    # asyncio.run(main()) # main is no longer async
    main()
