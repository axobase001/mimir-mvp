"""Citation graph skill — explore citation networks via Semantic Scholar API."""

import logging
from urllib.parse import quote

import httpx

from ..base import Skill

log = logging.getLogger(__name__)

_S2_API_BASE = "https://api.semanticscholar.org/graph/v1/paper/"


class CitationGraphSkill(Skill):
    """Explore paper citation graphs using the Semantic Scholar API."""

    def __init__(self):
        super().__init__()

    # ── Properties ──

    @property
    def name(self) -> str:
        return "citation_graph"

    @property
    def description(self) -> str:
        return "Look up papers and explore citation graphs via Semantic Scholar"

    @property
    def capabilities(self) -> list[str]:
        return ["citation_analysis", "influence_tracking", "literature_graph"]

    @property
    def risk_level(self) -> str:
        return "safe"

    @property
    def param_schema(self) -> dict:
        return {
            "action": {
                "type": "str",
                "required": True,
                "description": "One of: lookup, citations, references",
            },
            "paper_id": {
                "type": "str",
                "required": False,
                "description": "Semantic Scholar paper ID, DOI, or arXiv ID (e.g. 'ARXIV:2301.07041')",
            },
            "title": {
                "type": "str",
                "required": False,
                "description": "Paper title for search-based lookup",
            },
        }

    # ── Execution ──

    async def execute(self, params: dict) -> dict:
        action = params.get("action", "lookup")

        if action == "lookup":
            return await self._lookup(params)
        elif action == "citations":
            return await self._citations(params)
        elif action == "references":
            return await self._references(params)
        else:
            return {"success": False, "result": "", "error": f"Unknown action: {action}"}

    async def _lookup(self, params: dict) -> dict:
        paper_id = params.get("paper_id", "").strip()
        title = params.get("title", "").strip()

        if not paper_id and not title:
            return {
                "success": False,
                "result": "",
                "error": "Either 'paper_id' or 'title' is required",
            }

        if paper_id:
            url = f"{_S2_API_BASE}{quote(paper_id, safe=':')}"
        else:
            # Use title search via the search endpoint
            url = f"{_S2_API_BASE}search"

        fields = "paperId,title,authors,year,abstract,citationCount,referenceCount,url"

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                if paper_id:
                    resp = await client.get(url, params={"fields": fields})
                else:
                    resp = await client.get(
                        url, params={"query": title, "fields": fields, "limit": 3}
                    )
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            log.warning("Semantic Scholar lookup failed: %s", e)
            return {"success": False, "result": "", "error": str(e)}

        if paper_id:
            return {
                "success": True,
                "result": self._format_paper(data),
                "error": None,
            }
        else:
            papers = data.get("data", [])
            if not papers:
                return {
                    "success": True,
                    "result": "No papers found.",
                    "error": None,
                }
            text = "\n\n".join(self._format_paper(p) for p in papers)
            return {"success": True, "result": text, "error": None}

    async def _citations(self, params: dict) -> dict:
        paper_id = params.get("paper_id", "").strip()
        if not paper_id:
            return {"success": False, "result": "", "error": "'paper_id' is required"}

        url = f"{_S2_API_BASE}{quote(paper_id, safe=':')}/citations"
        fields = "title,authors,year,citationCount,url"

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(
                    url, params={"fields": fields, "limit": 20}
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            log.warning("Semantic Scholar citations failed: %s", e)
            return {"success": False, "result": "", "error": str(e)}

        citing = data.get("data", [])
        if not citing:
            return {
                "success": True,
                "result": "No citing papers found.",
                "error": None,
            }

        lines = []
        for item in citing:
            p = item.get("citingPaper", {})
            authors = ", ".join(
                a.get("name", "") for a in (p.get("authors") or [])[:3]
            )
            year = p.get("year", "?")
            title = p.get("title", "N/A")
            cites = p.get("citationCount", 0)
            lines.append(f"- [{year}] {title} ({authors}) — {cites} citations")

        return {
            "success": True,
            "result": f"Citing papers ({len(citing)}):\n" + "\n".join(lines),
            "error": None,
        }

    async def _references(self, params: dict) -> dict:
        paper_id = params.get("paper_id", "").strip()
        if not paper_id:
            return {"success": False, "result": "", "error": "'paper_id' is required"}

        url = f"{_S2_API_BASE}{quote(paper_id, safe=':')}/references"
        fields = "title,authors,year,citationCount,url"

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.get(
                    url, params={"fields": fields, "limit": 20}
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            log.warning("Semantic Scholar references failed: %s", e)
            return {"success": False, "result": "", "error": str(e)}

        refs = data.get("data", [])
        if not refs:
            return {
                "success": True,
                "result": "No references found.",
                "error": None,
            }

        lines = []
        for item in refs:
            p = item.get("citedPaper", {})
            authors = ", ".join(
                a.get("name", "") for a in (p.get("authors") or [])[:3]
            )
            year = p.get("year", "?")
            title = p.get("title", "N/A")
            cites = p.get("citationCount", 0)
            lines.append(f"- [{year}] {title} ({authors}) — {cites} citations")

        return {
            "success": True,
            "result": f"References ({len(refs)}):\n" + "\n".join(lines),
            "error": None,
        }

    @staticmethod
    def _format_paper(paper: dict) -> str:
        """Format a single paper dict into readable text."""
        title = paper.get("title", "N/A")
        year = paper.get("year", "?")
        authors = ", ".join(
            a.get("name", "") for a in (paper.get("authors") or [])[:5]
        )
        abstract = (paper.get("abstract") or "")[:300]
        cites = paper.get("citationCount", 0)
        refs = paper.get("referenceCount", 0)
        pid = paper.get("paperId", "")
        s2_url = paper.get("url", "")

        return (
            f"**{title}** ({year})\n"
            f"  Authors: {authors}\n"
            f"  Citations: {cites} | References: {refs}\n"
            f"  S2 ID: {pid}\n"
            f"  URL: {s2_url}\n"
            f"  Abstract: {abstract}..."
        )
