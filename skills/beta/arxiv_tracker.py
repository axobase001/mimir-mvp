"""arXiv tracker skill — search and track papers via the arXiv API."""

import logging
import xml.etree.ElementTree as ET
from typing import Any

import httpx

from ..base import Skill

log = logging.getLogger(__name__)

_ARXIV_API = "http://export.arxiv.org/api/query"
_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_ARXIV_NS = "{http://arxiv.org/schemas/atom}"


class ArxivTrackerSkill(Skill):
    """Search arXiv and track recurring queries for new papers."""

    def __init__(self):
        super().__init__()
        self._tracked_queries: dict[str, dict[str, Any]] = {}
        # {label: {query, categories, max_results, last_seen_ids}}

    # ── Properties ──

    @property
    def name(self) -> str:
        return "arxiv_tracker"

    @property
    def description(self) -> str:
        return "Search arXiv for papers and track queries for recurring checks"

    @property
    def capabilities(self) -> list[str]:
        return ["arxiv_search", "paper_discovery", "research_tracking"]

    @property
    def risk_level(self) -> str:
        return "safe"

    @property
    def param_schema(self) -> dict:
        return {
            "action": {
                "type": "str",
                "required": True,
                "description": "One of: search, track, recent",
            },
            "query": {
                "type": "str",
                "required": False,
                "description": "Search query string (for search/track)",
            },
            "max_results": {
                "type": "int",
                "required": False,
                "default": 5,
                "description": "Maximum number of results",
            },
            "categories": {
                "type": "str",
                "required": False,
                "description": "arXiv category filter, e.g. 'cs.AI' (for search/track)",
            },
            "label": {
                "type": "str",
                "required": False,
                "description": "Label for tracked query (for track)",
            },
        }

    # ── Execution ──

    async def execute(self, params: dict) -> dict:
        action = params.get("action", "search")

        if action == "search":
            return await self._search(params)
        elif action == "track":
            return self._track(params)
        elif action == "recent":
            return await self._recent()
        else:
            return {"success": False, "result": "", "error": f"Unknown action: {action}"}

    async def _search(self, params: dict) -> dict:
        query = params.get("query", "").strip()
        if not query:
            return {"success": False, "result": "", "error": "'query' is required"}

        max_results = params.get("max_results", 5)
        categories = params.get("categories", "")

        search_query = self._build_query(query, categories)
        papers = await self._fetch_arxiv(search_query, max_results)

        if not papers:
            return {"success": True, "result": "No papers found.", "error": None}

        text = "\n\n".join(
            f"- **{p['title']}**\n"
            f"  Authors: {p['authors']}\n"
            f"  Published: {p['published']}\n"
            f"  arXiv: {p['id']}\n"
            f"  Summary: {p['summary'][:200]}..."
            for p in papers
        )
        return {"success": True, "result": text, "error": None}

    def _track(self, params: dict) -> dict:
        query = params.get("query", "").strip()
        label = params.get("label", "").strip() or query
        if not query:
            return {"success": False, "result": "", "error": "'query' is required"}

        self._tracked_queries[label] = {
            "query": query,
            "categories": params.get("categories", ""),
            "max_results": params.get("max_results", 5),
            "last_seen_ids": set(),
        }
        return {
            "success": True,
            "result": f"Now tracking query '{label}': {query}",
            "error": None,
        }

    async def _recent(self) -> dict:
        if not self._tracked_queries:
            return {
                "success": True,
                "result": "No tracked queries. Use 'track' action first.",
                "error": None,
            }

        report: list[str] = []
        for label, info in self._tracked_queries.items():
            search_query = self._build_query(info["query"], info["categories"])
            papers = await self._fetch_arxiv(search_query, info["max_results"])

            new_papers = [p for p in papers if p["id"] not in info["last_seen_ids"]]
            info["last_seen_ids"].update(p["id"] for p in papers)

            if new_papers:
                items = "\n".join(
                    f"    - {p['title']} ({p['id']})" for p in new_papers
                )
                report.append(f"- {label}: {len(new_papers)} new papers\n{items}")
            else:
                report.append(f"- {label}: no new papers")

        return {"success": True, "result": "\n\n".join(report), "error": None}

    # ── Helpers ──

    @staticmethod
    def _build_query(query: str, categories: str = "") -> str:
        """Build arXiv API search_query string."""
        parts = [f"all:{query}"]
        if categories:
            parts.append(f"cat:{categories}")
        return "+AND+".join(parts)

    @staticmethod
    async def _fetch_arxiv(search_query: str, max_results: int) -> list[dict]:
        """Fetch and parse arXiv API results."""
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(
                    _ARXIV_API,
                    params={
                        "search_query": search_query,
                        "start": 0,
                        "max_results": max_results,
                        "sortBy": "submittedDate",
                        "sortOrder": "descending",
                    },
                )
                resp.raise_for_status()
        except Exception as e:
            log.warning("arXiv API request failed: %s", e)
            return []

        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError:
            return []

        papers: list[dict] = []
        for entry in root.findall(f"{_ATOM_NS}entry"):
            paper_id = ""
            title = ""
            summary = ""
            published = ""
            authors: list[str] = []

            id_el = entry.find(f"{_ATOM_NS}id")
            if id_el is not None and id_el.text:
                paper_id = id_el.text.strip()

            title_el = entry.find(f"{_ATOM_NS}title")
            if title_el is not None and title_el.text:
                title = " ".join(title_el.text.split())

            summary_el = entry.find(f"{_ATOM_NS}summary")
            if summary_el is not None and summary_el.text:
                summary = " ".join(summary_el.text.split())

            pub_el = entry.find(f"{_ATOM_NS}published")
            if pub_el is not None and pub_el.text:
                published = pub_el.text.strip()[:10]

            for author_el in entry.findall(f"{_ATOM_NS}author"):
                name_el = author_el.find(f"{_ATOM_NS}name")
                if name_el is not None and name_el.text:
                    authors.append(name_el.text.strip())

            papers.append({
                "id": paper_id,
                "title": title,
                "authors": ", ".join(authors),
                "summary": summary,
                "published": published,
            })

        return papers
