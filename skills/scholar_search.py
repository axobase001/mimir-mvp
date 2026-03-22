"""ScholarSearchSkill — find researcher emails via Google Scholar + MX/SMTP verification."""

from __future__ import annotations

import logging
import re
from typing import Optional

import httpx

from .base import Skill
from .email_skill import verify_email

log = logging.getLogger(__name__)

# Common academic email patterns: first.last, f.last, first.m.last, flast
_NAME_PATTERNS = [
    "{first}.{last}",
    "{f}.{last}",
    "{first}.{m}.{last}",
    "{f}.{m}.{last}",
    "{first}{last}",
    "{f}{last}",
    "{last}.{first}",
    "{last}{f}",
]


def _generate_candidates(name: str, domain: str) -> list[str]:
    """Generate email candidates from a name and domain."""
    parts = name.lower().strip().split()
    if len(parts) < 2:
        return [f"{parts[0]}@{domain}"] if parts else []

    first = parts[0]
    last = parts[-1]
    f = first[0]
    m = parts[1][0] if len(parts) > 2 else ""

    candidates = []
    for pat in _NAME_PATTERNS:
        try:
            addr = pat.format(first=first, last=last, f=f, m=m)
            if ".." not in addr and addr:  # skip patterns with empty middle initial
                candidates.append(f"{addr}@{domain}")
        except (KeyError, IndexError):
            continue

    return list(dict.fromkeys(candidates))  # dedupe preserving order


class ScholarSearchSkill(Skill):
    """Find researcher email via Google Scholar domain + email pattern verification."""

    def __init__(self, searxng_url: str = "http://localhost:8080/search", contact_registry=None) -> None:
        super().__init__()
        self._searxng_url = searxng_url
        self._contact_registry = contact_registry
        self._call_count = 0
        self._success_count = 0

    @property
    def name(self) -> str:
        return "scholar_search"

    @property
    def description(self) -> str:
        return "Search for a researcher's verified email via Google Scholar and DNS/SMTP verification"

    @property
    def capabilities(self) -> list[str]:
        return ["find_email", "researcher_lookup", "scholar_search"]

    @property
    def param_schema(self) -> dict:
        return {
            "person_name": {"type": "str", "required": True,
                            "description": "Full name of the person to find"},
            "field": {"type": "str", "required": False, "default": "",
                      "description": "Research field or keywords"},
            "institution": {"type": "str", "required": False, "default": "",
                            "description": "Known institution (optional)"},
        }

    @property
    def risk_level(self) -> str:
        return "safe"

    async def execute(self, params: dict) -> dict:
        person_name = (params.get("person_name") or "").strip()
        field = (params.get("field") or "").strip()
        institution = (params.get("institution") or "").strip()
        self._call_count += 1

        if not person_name:
            return {"success": False, "result": "", "error": "No person name provided"}

        # Step 1: Search Google Scholar for verified domain
        # If institution given, search with it to disambiguate common names
        query = f"{person_name} {institution} {field}".strip() if institution else f"{person_name} {field}".strip()
        domain = await self._find_scholar_domain(query, person_name)

        # If institution was given but Scholar found a different domain, try university fallback first
        if domain and institution:
            inst_lower = institution.lower()
            # Check if found domain matches the expected institution
            if not any(kw in domain.lower() for kw in inst_lower.split()):
                # Mismatch — try university domain instead, keep Scholar domain as backup
                backup_domain = domain
                domain = await self._find_university_domain(person_name, institution)
                if not domain:
                    domain = backup_domain  # fall back to Scholar domain

        if not domain:
            domain = await self._find_university_domain(person_name, institution)

        if not domain:
            msg = f"Could not find verified email domain for {person_name}. Try searching their institution staff page manually."
            return {"success": False, "result": msg, "error": None}

        # Step 2: Try to find REAL email from web pages first (don't guess)
        real_email = await self._find_email_from_web(person_name, domain, institution)
        if real_email:
            valid, reason = verify_email(real_email)
            if valid:
                self._success_count += 1
                result = {
                    "name": person_name,
                    "institution": institution or domain,
                    "domain": domain,
                    "email": real_email,
                    "source": f"Extracted from webpage + {'SMTP' if 'SMTP' in reason else 'MX'} verified",
                    "status": "ready_to_contact",
                    "verification": reason,
                }
                log.info("ScholarSearch: found real email %s → %s", person_name, real_email)

                # Auto-register in contact registry
                if self._contact_registry:
                    from ..core.contact_registry import Contact
                    self._contact_registry.add_contact(Contact(
                        name=person_name,
                        email=real_email,
                        institution=institution or domain,
                        field=field,
                        status="ready",
                        relevance="high",
                    ))

                return {
                    "success": True,
                    "result": (
                        f"Found verified email for {person_name}: {real_email}\n"
                        f"Domain: {domain}\n"
                        f"Source: extracted from web page (not guessed)\n"
                        f"Verification: {reason}\n"
                        f"Status: ready_to_contact"
                    ),
                    "error": None,
                    "contact": result,
                }

        # Step 3: Fallback — generate candidates from name patterns and verify
        candidates = _generate_candidates(person_name, domain)
        log.info("ScholarSearch: no email found on web, trying %d format candidates for %s@%s",
                 len(candidates), person_name, domain)

        for addr in candidates:
            valid, reason = verify_email(addr)
            if valid and "SMTP accepted" in reason:
                # Only use guessed format if SMTP confirms it exists
                self._success_count += 1
                result = {
                    "name": person_name,
                    "institution": institution or domain,
                    "domain": domain,
                    "email": addr,
                    "source": f"Name pattern + SMTP verified (confirmed exists)",
                    "status": "ready_to_contact",
                    "verification": reason,
                }
                log.info("ScholarSearch: SMTP-confirmed %s → %s", person_name, addr)

                # Auto-register in contact registry
                if self._contact_registry:
                    from ..core.contact_registry import Contact
                    self._contact_registry.add_contact(Contact(
                        name=person_name,
                        email=addr,
                        institution=institution or domain,
                        field=field,
                        status="ready",
                        relevance="high",
                    ))

                return {
                    "success": True,
                    "result": (
                        f"Found verified email for {person_name}: {addr}\n"
                        f"Domain: {domain}\n"
                        f"Verification: {reason}\n"
                        f"Status: ready_to_contact"
                    ),
                    "error": None,
                    "contact": result,
                }

        # Could not find or verify any email
        msg = (
            f"Found domain {domain} for {person_name}, "
            f"but could not find their exact email on any web page, "
            f"and format guessing could not be SMTP-confirmed. "
            f"Status: needs_manual_review. "
            f"Try a different person."
        )

        # Register as needs_manual_review so we don't keep retrying
        if self._contact_registry:
            from ..core.contact_registry import Contact
            self._contact_registry.add_contact(Contact(
                name=person_name,
                email=f"unknown@{domain}",
                institution=institution or domain,
                field=field,
                status="rejected",
                relevance="medium",
            ))

        return {"success": False, "result": msg, "error": None}

    async def _find_email_from_web(
        self, name: str, domain: str, institution: str,
    ) -> Optional[str]:
        """Search web pages for the person's actual email address.

        Strategy: search "{name}" email @{domain} → fetch top results →
        regex extract emails matching the domain → return first match.
        """
        queries = [
            f'"{name}" email @{domain}',
            f'{name} {institution} contact email',
            f'{name} {domain} staff page',
        ]

        _EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]+@' + re.escape(domain))

        for query in queries:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(
                        self._searxng_url,
                        params={"q": query, "format": "json", "language": "en"},
                    )
                    if resp.status_code != 200:
                        continue

                results = resp.json().get("results", [])

                # Check search snippets first (fast)
                for r in results[:10]:
                    content = r.get("content", "") + " " + r.get("title", "")
                    found = _EMAIL_RE.findall(content)
                    for addr in found:
                        addr = addr.lower()
                        if addr.startswith(("info@", "admin@", "webmaster@", "support@")):
                            continue
                        log.info("ScholarSearch: found email in snippet: %s", addr)
                        return addr

                # Fetch top 3 pages and scan HTML
                for r in results[:3]:
                    url = r.get("url", "")
                    if not url.startswith("http"):
                        continue
                    try:
                        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                            page_resp = await client.get(url, headers={
                                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                            })
                            if page_resp.status_code == 200:
                                html = page_resp.text[:200000]
                                # Also decode HTML entities
                                html_decoded = html.replace("&#64;", "@").replace("&#x40;", "@").replace("[at]", "@")
                                found = _EMAIL_RE.findall(html_decoded)
                                for addr in found:
                                    addr = addr.lower()
                                    if addr.startswith(("info@", "admin@", "webmaster@", "support@", "noreply@")):
                                        continue
                                    log.info("ScholarSearch: found email on page %s: %s", url[:60], addr)
                                    return addr
                    except Exception:
                        continue

            except Exception as e:
                log.warning("Email web search failed for query '%s': %s", query[:40], e)
                continue

        return None

    async def _find_scholar_domain(self, query: str, name: str) -> Optional[str]:
        """Search for 'Verified email at {domain}' in Google Scholar snippets."""
        search_query = f"{name} Google Scholar"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    self._searxng_url,
                    params={"q": search_query, "format": "json", "language": "en"},
                )
                if resp.status_code != 200:
                    return None

            results = resp.json().get("results", [])
            for r in results:
                # Google Scholar snippet pattern: "Verified email at sussex.ac.uk"
                content = r.get("content", "") + " " + r.get("title", "")
                match = re.search(r"[Vv]erified email at (\S+)", content)
                if match:
                    domain = match.group(1).rstrip(".")
                    # Strip www. prefix — emails use bare domain
                    if domain.startswith("www."):
                        domain = domain[4:]
                    log.info("ScholarSearch: found domain %s from Scholar for %s", domain, name)
                    return domain

                # Also check for "email at domain" patterns
                match2 = re.search(r"email[:\s]+\w+@([\w.]+\.\w{2,})", content, re.IGNORECASE)
                if match2:
                    return match2.group(1)

        except Exception as e:
            log.warning("Scholar search failed: %s", e)
        return None

    async def _find_university_domain(self, name: str, institution: str) -> Optional[str]:
        """Fallback: search for person's university page and extract domain."""
        query = f"{name} {institution} professor email" if institution else f"{name} professor university"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    self._searxng_url,
                    params={"q": query, "format": "json", "language": "en"},
                )
                if resp.status_code != 200:
                    return None

            results = resp.json().get("results", [])
            # Look for .edu or .ac.uk domains in result URLs
            for r in results:
                url = r.get("url", "")
                for pattern in [r"([\w.]+\.edu)", r"([\w.]+\.ac\.\w+)", r"([\w.]+\.edu\.\w+)"]:
                    match = re.search(pattern, url)
                    if match:
                        domain = match.group(1)
                        if domain.startswith("www."):
                            domain = domain[4:]
                        log.info("ScholarSearch: found university domain %s from URL for %s", domain, name)
                        return domain

        except Exception as e:
            log.warning("University domain search failed: %s", e)
        return None

    @property
    def usage_stats(self) -> dict:
        return {
            "call_count": self._call_count,
            "success_count": self._success_count,
        }
