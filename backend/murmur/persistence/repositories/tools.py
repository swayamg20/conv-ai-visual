"""Repository for model-callable tool definitions."""

import json
from datetime import datetime

from sqlalchemy import func
from sqlmodel import select

from murmur.persistence.clock import utc_now
from murmur.persistence.database import get_session
from murmur.persistence.models import ToolModel


class ToolRepo:
    """Persist tools and expose provider-specific schemas."""

    _openai_schema_cache: list[dict] | None = None
    _openai_schema_cache_key: tuple[int, datetime | None] | None = None

    @classmethod
    def _invalidate_schema_cache(cls) -> None:
        cls._openai_schema_cache = None
        cls._openai_schema_cache_key = None

    @staticmethod
    def _get_enabled_schema_cache_key() -> tuple[int, datetime | None]:
        with get_session() as session:
            statement = select(
                func.count(ToolModel.name),
                func.max(ToolModel.updated_at),
            ).where(ToolModel.enabled.is_(True))
            count, latest_updated_at = session.exec(statement).one()
            return int(count or 0), latest_updated_at

    @classmethod
    def upsert(
        cls,
        name: str,
        description: str,
        parameters: dict,
        handler_module: str | None = None,
        handler_function: str | None = None,
        code: str | None = None,
        enabled: bool = True,
    ) -> ToolModel:
        with get_session() as session:
            tool = session.get(ToolModel, name)
            if tool:
                tool.description = description
                tool.parameters_json = json.dumps(parameters)
                tool.handler_module = handler_module
                tool.handler_function = handler_function
                tool.code = code
                tool.enabled = enabled
                tool.updated_at = utc_now()
            else:
                tool = ToolModel(
                    name=name,
                    description=description,
                    parameters_json=json.dumps(parameters),
                    handler_module=handler_module,
                    handler_function=handler_function,
                    code=code,
                    enabled=enabled,
                )
            session.add(tool)
            session.commit()
            session.refresh(tool)
            cls._invalidate_schema_cache()
            return tool

    @staticmethod
    def get(name: str) -> ToolModel | None:
        with get_session() as session:
            return session.get(ToolModel, name)

    @staticmethod
    def get_enabled(name: str) -> ToolModel | None:
        with get_session() as session:
            tool = session.get(ToolModel, name)
            return tool if tool and tool.enabled else None

    @staticmethod
    def list_all(enabled_only: bool = True) -> list[ToolModel]:
        with get_session() as session:
            statement = select(ToolModel).order_by(ToolModel.name)
            if enabled_only:
                statement = statement.where(ToolModel.enabled.is_(True))
            return list(session.exec(statement).all())

    @classmethod
    def delete(cls, name: str) -> bool:
        with get_session() as session:
            tool = session.get(ToolModel, name)
            if not tool:
                return False
            session.delete(tool)
            session.commit()
            cls._invalidate_schema_cache()
            return True

    @classmethod
    def set_enabled(cls, name: str, enabled: bool) -> bool:
        with get_session() as session:
            tool = session.get(ToolModel, name)
            if not tool:
                return False
            tool.enabled = enabled
            tool.updated_at = utc_now()
            session.add(tool)
            session.commit()
            cls._invalidate_schema_cache()
            return True

    @classmethod
    def to_openai_format(cls) -> list[dict]:
        """Return enabled tools in OpenAI format, using a mutation-aware cache."""
        cache_key = cls._get_enabled_schema_cache_key()
        if cls._openai_schema_cache is not None and cls._openai_schema_cache_key == cache_key:
            return cls._openai_schema_cache

        formatted = [tool.to_openai_schema() for tool in cls.list_all(enabled_only=True)]
        cls._openai_schema_cache = formatted
        cls._openai_schema_cache_key = cache_key
        return formatted

    @classmethod
    def to_anthropic_format(cls) -> list[dict]:
        """Return enabled tools in Anthropic format."""
        return [tool.to_anthropic_schema() for tool in cls.list_all(enabled_only=True)]
