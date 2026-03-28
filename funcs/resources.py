"""
Resource ingestion — PDF and URL extraction, chunking, and search.
"""
import os
import logging

import pymupdf  # pymupdf (import as pymupdf, not fitz, to avoid conflicts)
import httpx
from bs4 import BeautifulSoup

from .models import (
    ResourceModel,
    ResourceChunkModel,
    ResourceRepo,
    ResourceChunkRepo,
)

logger = logging.getLogger(__name__)

CHUNK_TARGET_CHARS = 2000


def _chunk_text(text: str, target_chars: int = CHUNK_TARGET_CHARS) -> list[str]:
    """Split text into chunks of roughly `target_chars` by merging paragraphs."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        if current and len(current) + len(para) + 2 > target_chars:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para

    if current:
        chunks.append(current)

    # If a single chunk is still too large, split on newlines
    final: list[str] = []
    for chunk in chunks:
        if len(chunk) <= target_chars * 1.5:
            final.append(chunk)
        else:
            lines = chunk.split("\n")
            buf = ""
            for line in lines:
                if buf and len(buf) + len(line) + 1 > target_chars:
                    final.append(buf)
                    buf = line
                else:
                    buf = f"{buf}\n{line}" if buf else line
            if buf:
                final.append(buf)

    return final


def ingest_pdf(file_path: str, agent_id: str, user_id: str) -> ResourceModel:
    """Extract text from a PDF, chunk it, and store in the database."""
    file_size = os.path.getsize(file_path)
    filename = os.path.basename(file_path)

    resource = ResourceRepo.create(
        agent_id=agent_id,
        user_id=user_id,
        name=filename,
        resource_type="pdf",
        size_bytes=file_size,
        status="processing",
    )

    try:
        doc = pymupdf.open(file_path)
        full_text_parts: list[str] = []
        page_texts: list[tuple] = []  # (page_number, text)

        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text()
            if text.strip():
                full_text_parts.append(text)
                page_texts.append((page_num + 1, text))

        doc.close()
        full_text = "\n\n".join(full_text_parts)

        # Chunk by paragraphs, tracking page numbers
        chunk_models: list[ResourceChunkModel] = []
        chunk_index = 0

        for page_num, page_text in page_texts:
            page_chunks = _chunk_text(page_text)
            for chunk_content in page_chunks:
                chunk_models.append(ResourceChunkModel(
                    resource_id=resource.id,
                    chunk_index=chunk_index,
                    content=chunk_content,
                    page_number=page_num,
                ))
                chunk_index += 1

        ResourceChunkRepo.create_batch(chunk_models)
        ResourceRepo.update_status(
            resource.id,
            status="ready",
            content_text=full_text,
            chunk_count=len(chunk_models),
        )
        # Refresh to return updated object
        resource = ResourceRepo.get_by_id(resource.id)

    except Exception as e:
        logger.error("PDF ingestion failed for %s: %s", file_path, e)
        ResourceRepo.update_status(resource.id, status="failed")
        resource = ResourceRepo.get_by_id(resource.id)

    return resource


async def ingest_url(url: str, agent_id: str, user_id: str) -> ResourceModel:
    """Fetch a URL, extract text content, chunk it, and store."""
    resource = ResourceRepo.create(
        agent_id=agent_id,
        user_id=user_id,
        name=url,
        resource_type="url",
        status="processing",
    )

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        html = resp.text
        resource_size = len(html.encode("utf-8"))
        soup = BeautifulSoup(html, "html.parser")

        # Remove script and style elements
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        full_text = soup.get_text(separator="\n\n", strip=True)
        chunks = _chunk_text(full_text)

        chunk_models = [
            ResourceChunkModel(
                resource_id=resource.id,
                chunk_index=i,
                content=chunk,
                page_number=None,
            )
            for i, chunk in enumerate(chunks)
        ]

        ResourceChunkRepo.create_batch(chunk_models)
        ResourceRepo.update_status(
            resource.id,
            status="ready",
            content_text=full_text,
            chunk_count=len(chunk_models),
            size_bytes=resource_size,
        )
        resource = ResourceRepo.get_by_id(resource.id)

    except Exception as e:
        logger.error("URL ingestion failed for %s: %s", url, e)
        ResourceRepo.update_status(resource.id, status="failed")
        resource = ResourceRepo.get_by_id(resource.id)

    return resource


def search_chunks(agent_id: str, query: str, limit: int = 5) -> list[ResourceChunkModel]:
    """Search resource chunks for the given agent using hybrid lexical ranking."""
    chunks = ResourceChunkRepo.search(agent_id, query, limit=limit)
    logger.debug(
        "search_chunks agent_id=%s query_len=%d returned=%d",
        agent_id,
        len(query),
        len(chunks),
    )
    return chunks
