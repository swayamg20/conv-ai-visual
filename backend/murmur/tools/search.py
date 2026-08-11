"""
Web search integration via Tavily.
"""

import logging

from murmur.core.config import config
from murmur.persistence.repositories.tools import ToolRepo

logger = logging.getLogger(__name__)


async def web_search(query: str, max_results: int = 5) -> str:
    """Search the web and return formatted results for the LLM."""
    if not config.TAVILY_API_KEY:
        return "Web search is not configured. Please set TAVILY_API_KEY."

    try:
        from tavily import TavilyClient

        client = TavilyClient(api_key=config.TAVILY_API_KEY)
        results = client.search(query, max_results=max_results)

        formatted = []
        for r in results.get("results", []):
            formatted.append(f"**{r['title']}**\n{r['content']}\nSource: {r['url']}\n")

        return "\n---\n".join(formatted) if formatted else "No results found."
    except Exception as e:
        logger.error("Web search error: %s", e, exc_info=True)
        return f"Web search failed: {e}"


def register_web_search_tool() -> None:
    """Register the web_search tool in the DB tool registry."""
    ToolRepo.upsert(
        name="web_search",
        description=(
            "Search the web for current information. Use when asked about recent events, "
            "facts you're unsure about, or anything requiring up-to-date data."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                }
            },
            "required": ["query"],
        },
        handler_module="murmur.tools.search",
        handler_function="web_search",
        enabled=True,
    )
    logger.info("Registered web_search tool in DB")
