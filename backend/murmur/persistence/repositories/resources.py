"""Repositories and lexical retrieval for agent resources."""

import logging
import re
from collections import Counter

from sqlalchemy import func, or_
from sqlmodel import select

from murmur.persistence.database import get_session
from murmur.persistence.models import ResourceChunkModel, ResourceModel

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "our",
    "please",
    "that",
    "the",
    "their",
    "this",
    "to",
    "was",
    "we",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "you",
}


def _query_terms(query: str) -> list[str]:
    terms: list[str] = []
    for token in _TOKEN_RE.findall(query.lower()):
        if len(token) < 2 or token in _STOPWORDS or token in terms:
            continue
        terms.append(token)
    return terms


def _score_chunk(content: str, query_terms: list[str], normalized_query: str) -> float:
    if not content or not query_terms:
        return 0.0

    content_lower = content.lower()
    token_counts = Counter(_TOKEN_RE.findall(content_lower))
    if not token_counts:
        return 0.0

    matched_terms = 0
    term_hits = 0.0
    for term in query_terms:
        occurrences = token_counts.get(term, 0)
        if occurrences:
            matched_terms += 1
            term_hits += min(occurrences, 3)
    if not matched_terms:
        return 0.0

    coverage = matched_terms / len(query_terms)
    density = term_hits / max(1.0, len(token_counts) ** 0.5)
    score = (coverage * 4.0) + (density * 2.0)
    if normalized_query and normalized_query in content_lower:
        score += 2.5
    score += min(sum(1 for term in query_terms if term in content_lower[:400]), 3) * 0.35
    if len(content) < 400:
        score += 0.25
    return score


class ResourceRepo:
    """Persist uploaded and linked agent resources."""

    @staticmethod
    def create(**kwargs) -> ResourceModel:
        with get_session() as session:
            resource = ResourceModel(**kwargs)
            session.add(resource)
            session.commit()
            session.refresh(resource)
            return resource

    @staticmethod
    def get_by_id(resource_id: str) -> ResourceModel | None:
        with get_session() as session:
            return session.get(ResourceModel, resource_id)

    @staticmethod
    def list_by_agent(agent_id: str) -> list[ResourceModel]:
        with get_session() as session:
            statement = (
                select(ResourceModel)
                .where(ResourceModel.agent_id == agent_id)
                .order_by(ResourceModel.created_at.desc())
            )
            return list(session.exec(statement).all())

    @staticmethod
    def update_status(resource_id: str, status: str, **kwargs) -> ResourceModel | None:
        with get_session() as session:
            resource = session.get(ResourceModel, resource_id)
            if not resource:
                return None
            resource.status = status
            for key, value in kwargs.items():
                if hasattr(resource, key):
                    setattr(resource, key, value)
            session.add(resource)
            session.commit()
            session.refresh(resource)
            return resource

    @staticmethod
    def delete(resource_id: str) -> bool:
        with get_session() as session:
            resource = session.get(ResourceModel, resource_id)
            if not resource:
                return False
            chunks = session.exec(
                select(ResourceChunkModel).where(ResourceChunkModel.resource_id == resource_id)
            ).all()
            for chunk in chunks:
                session.delete(chunk)
            session.delete(resource)
            session.commit()
            return True


class ResourceChunkRepo:
    """Persist and retrieve resource chunks."""

    @staticmethod
    def create_batch(chunks: list[ResourceChunkModel]) -> None:
        with get_session() as session:
            for chunk in chunks:
                session.add(chunk)
            session.commit()

    @staticmethod
    def search(agent_id: str, query: str, limit: int = 5) -> list[ResourceChunkModel]:
        """Filter lexical candidates in SQL and rerank them in process."""
        with get_session() as session:
            resource_ids = list(
                session.exec(
                    select(ResourceModel.id).where(
                        ResourceModel.agent_id == agent_id,
                        ResourceModel.status == "ready",
                    )
                ).all()
            )
            if not resource_ids:
                logger.debug("No ready resources for agent_id=%s", agent_id)
                return []

            query_terms = _query_terms(query)
            if not query_terms:
                logger.debug("Resource query has no meaningful terms for agent_id=%s", agent_id)
                return []

            conditions = [
                func.lower(ResourceChunkModel.content).like(f"%{term}%") for term in query_terms
            ]
            normalized_query = " ".join(query_terms)
            if len(query_terms) > 1:
                conditions.append(
                    func.lower(ResourceChunkModel.content).like(f"%{normalized_query}%")
                )

            candidates = list(
                session.exec(
                    select(ResourceChunkModel)
                    .where(
                        ResourceChunkModel.resource_id.in_(resource_ids),
                        or_(*conditions),
                    )
                    .order_by(ResourceChunkModel.resource_id, ResourceChunkModel.chunk_index)
                    .limit(max(limit * 20, 100))
                ).all()
            )
            ranked = sorted(
                (
                    (_score_chunk(chunk.content, query_terms, normalized_query), chunk)
                    for chunk in candidates
                ),
                key=lambda item: (-item[0], item[1].resource_id, item[1].chunk_index),
            )
            results = [chunk for score, chunk in ranked if score > 0][:limit]
            logger.debug(
                "Resource search agent_id=%s terms=%d candidates=%d returned=%d",
                agent_id,
                len(query_terms),
                len(candidates),
                len(results),
            )
            return results
