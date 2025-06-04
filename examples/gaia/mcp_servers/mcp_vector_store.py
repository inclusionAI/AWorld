import logging
import os
import sys
from typing import Any, Dict, List

import docx
import fitz
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from pymilvus import MilvusClient, model
from pymilvus.model.dense.onnx import OnnxEmbeddingFunction

# pylint: disable=W0707
# Initialize logger
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

# Initialize MCP server
mcp = FastMCP("vectorstore-server")

# Load environment variables
load_dotenv()

client = MilvusClient("mcp-milvus.db")
if client.has_collection(collection_name="mcp_collection"):
    client.drop_collection(collection_name="mcp_collection")
client.create_collection(
    collection_name="mcp_collection",
    dimension=768,  # The vectors we will use in this demo has 768 dimensions
)
embedding_fn: OnnxEmbeddingFunction = model.DefaultEmbeddingFunction()


@mcp.tool(description="Indexes a long text file (docx, pdf, txt) into Milvus by splitting and embedding.")
async def index_text_file(
    file_path: str = Field(description="Absolute path to the text file."),
) -> Dict[str, Any]:
    """
    Indexes a text file by splitting into chunks, embedding, and storing in Milvus.
    Returns the metadata of insertation operation.
    """
    if not os.path.exists(file_path):
        logger.error(f"File not found: {file_path}")
        raise FileNotFoundError(f"File not found: {file_path}")
    # manually set chunk size
    chunk_size = 512
    # Read and split file
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        try:
            doc = fitz.open(file_path)
            text = "\n".join([doc.load_page(i).get_text("text") for i in range(len(doc))])
            doc.close()
        except Exception as e:
            logger.error(f"PDF extraction failed: {e}")
            raise RuntimeError(f"PDF extraction failed: {e}")
    elif ext in [".docx"]:
        try:
            document = docx.Document(file_path)
            text = "\n".join([p.text for p in document.paragraphs])
        except Exception as e:
            logger.error(f"DOCX extraction failed: {e}")
            raise RuntimeError(f"DOCX extraction failed: {e}")
    else:
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
    # Chunk text
    chunks = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]

    embeddings = embedding_fn.encode_documents(chunks)
    # Insert into Milvus
    data = [
        {"id": i, "vector": embedding, "text": chunk, "subject": "record"}
        for i, (embedding, chunk) in enumerate(zip(embeddings, chunks))
    ]
    insert_resp = client.insert(collection_name="mcp_collection", data=data)
    return insert_resp


@mcp.tool(description="Searches Milvus for similar text chunks given a query.")
async def search_text(
    query: str = Field(description="Query string to search for."),
) -> List[Dict[str, Any]]:
    """
    Searches for similar text chunks in Milvus using semantic similarity.
    Returns a list of matching chunks with metadata.
    """
    embedding = embedding_fn.encode_documents([query])[0]
    results: List[List[dict]] = client.search(
        collection_name="mcp_collection",
        data=[embedding],
        output_fields=["text"],
    )
    output = []
    for hit in results[0]:
        output.append(
            {
                "score": float(hit.get("score", 0.0)),
                "text": hit.get("entity").get("text"),
            },
        )
    return output


@mcp.tool(description="Deletes all vectors for a given file from Milvus.")
async def delete_file_vectors(
    file_path: str = Field(description="Absolute path to the file whose vectors to delete."),
) -> int:
    """
    Deletes all vectors associated with a file from Milvus.
    Returns the number of deleted entities.
    """
    expr = f'file_path == "{file_path}"'
    num_deleted = client.delete(collection_name="mcp_collection", ids=expr)
    return num_deleted


@mcp.tool(description="Returns the number of vectors currently stored in Milvus.")
async def count_vectors() -> int:
    """
    Returns the total number of vectors in the collection.
    """
    return client.get_collection_stats(collection_name="mcp_collection").get("row_count", 0)


def main():
    """
    Main function to start the MCP server.
    """
    mcp.run(transport="stdio")


# Make the module callable for uvx


def __call__():
    main()


if __name__ != "__main__":
    sys.modules[__name__].__call__ = __call__

if __name__ == "__main__":
    main()
