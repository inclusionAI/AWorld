import logging
import os
import sys
import traceback
from datetime import datetime
from typing import List, Optional

import fitz
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from PIL import Image
from pydantic import BaseModel, Field
from PyPDF2 import PdfReader

load_dotenv()
logger = logging.getLogger(__name__)
mcp = FastMCP("pdf-server")


class DocumentError(BaseModel):
    """Model representing an error in document processing"""

    error: str
    file_path: Optional[str] = None
    file_name: Optional[str] = None


class PdfImage(BaseModel):
    """Model representing an image extracted from a PDF"""

    page: int
    format: str
    width: int
    height: int
    path: str


class PdfDocument(BaseModel):
    """Model representing a PDF document"""

    content: str
    file_path: str
    file_name: str
    page_count: int
    images: Optional[List[PdfImage]] = None
    image_count: Optional[int] = None
    image_dir: Optional[str] = None
    error: Optional[str] = None


class PdfResult(BaseModel):
    """Model representing results from processing multiple PDF documents"""

    total_files: int
    success_count: int
    failed_count: int
    results: List[PdfDocument]


def check_file_readable(document_path: str) -> str:
    """Check if file exists and is readable, return error message or None"""
    if not os.path.exists(document_path):
        return f"File does not exist: {document_path}"
    if not os.access(document_path, os.R_OK):
        return f"File is not readable: {document_path}"
    return None


def handle_error(e: Exception, error_type: str, file_path: Optional[str] = None) -> str:
    """Unified error handling and return standard format error message"""
    error_msg = f"{error_type} error: {str(e)}"
    logger.error(traceback.format_exc())

    error = DocumentError(
        error=error_msg,
        file_path=file_path,
        file_name=os.path.basename(file_path) if file_path else None,
    )

    return error.model_dump_json()


@mcp.tool(
    description="Read and return content from PDF file "
    "with optional image extraction."
)
def mcp_read_pdf_with_extracted_images(
    document_paths: List[str] = Field(description="The local input PDF file paths."),
    extract_images: bool = Field(
        default=False, description="Whether to extract images from PDF (default: False)"
    ),
) -> str:
    """Read and return content from PDF file with optional image extraction."""
    try:

        results = []
        success_count = 0
        failed_count = 0

        for document_path in document_paths:
            error = check_file_readable(document_path)
            if error:
                results.append(
                    PdfDocument(
                        content="",
                        file_path=document_path,
                        file_name=os.path.basename(document_path),
                        page_count=0,
                        error=error,
                    )
                )
                failed_count += 1
                continue

            try:
                with open(document_path, "rb") as f:
                    reader = PdfReader(f)
                    content = " ".join(page.extract_text() for page in reader.pages)
                    page_count = len(reader.pages)

                    pdf_result = PdfDocument(
                        content=content,
                        file_path=document_path,
                        file_name=os.path.basename(document_path),
                        page_count=page_count,
                    )

                    # Extract images if requested
                    if extract_images:
                        images_data = []
                        # Use /tmp directory for storing images
                        output_dir = "/tmp/pdf_images"

                        # Create output directory if it doesn't exist
                        os.makedirs(output_dir, exist_ok=True)

                        # Generate a unique subfolder based on filename to avoid conflicts
                        pdf_name = os.path.splitext(os.path.basename(document_path))[0]
                        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                        image_dir = os.path.join(output_dir, f"{pdf_name}_{timestamp}")

                        os.makedirs(image_dir, exist_ok=True)

                        try:
                            # Open PDF with PyMuPDF
                            pdf_document = fitz.open(document_path)

                            # Iterate through each page
                            for page_index, page in enumerate(pdf_document):
                                # Get image list
                                image_list = page.get_images(full=True)

                                # Process each image
                                for img_index, img in enumerate(image_list):
                                    # Extract image information
                                    xref = img[0]
                                    base_image = pdf_document.extract_image(xref)
                                    image_bytes = base_image["image"]
                                    image_ext = base_image["ext"]

                                    # Save image to file in /tmp directory
                                    img_filename = f"pdf_image_p{page_index+1}_{img_index+1}.{image_ext}"
                                    img_path = os.path.join(image_dir, img_filename)

                                    with open(img_path, "wb") as img_file:
                                        img_file.write(image_bytes)
                                        logger.info(f"Image saved: {img_path}")

                                    # Get image dimensions
                                    with Image.open(img_path) as img:
                                        width, height = img.size

                                    # Add to results with file path instead of base64
                                    images_data.append(
                                        PdfImage(
                                            page=page_index + 1,
                                            format=image_ext,
                                            width=width,
                                            height=height,
                                            path=img_path,
                                        )
                                    )

                            pdf_result.images = images_data
                            pdf_result.image_count = len(images_data)
                            pdf_result.image_dir = image_dir

                        except Exception as img_error:
                            logger.error(f"Error extracting images: {str(img_error)}")
                            # Don't clean up on error so we can keep any successfully extracted images
                            pdf_result.error = str(img_error)

                results.append(pdf_result)
                success_count += 1

            except Exception as e:
                results.append(
                    PdfDocument(
                        content="",
                        file_path=document_path,
                        file_name=os.path.basename(document_path),
                        page_count=0,
                        error=str(e),
                    )
                )
                failed_count += 1

        # Create final result
        pdf_result = PdfResult(
            total_files=len(document_paths),
            success_count=success_count,
            failed_count=failed_count,
            results=results,
        )

        return pdf_result.model_dump_json()

    except Exception as e:
        return handle_error(e, "PDF file reading")


def main():
    mcp.run(transport="stdio")


# Make the module callable
def __call__():
    """
    Make the module callable for uvx.
    This function is called when the module is executed directly.
    """
    main()


sys.modules[__name__].__call__ = __call__

# Run the server when the script is executed directly
if __name__ == "__main__":
    main()
