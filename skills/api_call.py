"""GenericAPISkill — universal HTTP API caller for Mimir.

Allows users to hit any REST endpoint without writing a dedicated skill.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .base import Skill

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 15.0


class GenericAPISkill(Skill):
    """Generic HTTP API caller with JSON path extraction."""

    def __init__(self, timeout: float = _DEFAULT_TIMEOUT) -> None:
        super().__init__()
        self._timeout = timeout
        self._call_count = 0
        self._success_count = 0

    @property
    def name(self) -> str:
        return "api_call"

    @property
    def description(self) -> str:
        return "通用HTTP API调用（GET/POST），支持JSON响应路径提取"

    @property
    def capabilities(self) -> list[str]:
        return ["api_call", "http_request", "external_service"]

    @property
    def param_schema(self) -> dict:
        return {
            "url": {"type": "str", "required": True,
                     "description": "Target URL"},
            "method": {"type": "str", "required": False, "default": "GET",
                        "description": "HTTP method: GET or POST"},
            "headers": {"type": "dict", "required": False, "default": {},
                          "description": "Custom request headers"},
            "body": {"type": "dict", "required": False, "default": {},
                      "description": "JSON body for POST requests"},
            "extract_path": {"type": "str", "required": False, "default": "",
                              "description": "Dot-separated path to extract from JSON response, e.g. 'data.price'"},
        }

    @property
    def risk_level(self) -> str:
        return "review"

    async def execute(self, params: dict) -> dict:
        url = params.get("url", "")
        method = params.get("method", "GET").upper()
        headers = params.get("headers", {})
        body = params.get("body", {})
        extract_path = params.get("extract_path", "")
        self._call_count += 1

        if not url:
            return {"success": False, "result": "", "error": "No URL provided"}

        if method not in ("GET", "POST"):
            return {"success": False, "result": "",
                    "error": f"Unsupported method: {method}. Use GET or POST."}

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
            ) as client:
                if method == "GET":
                    resp = await client.get(url, headers=headers)
                else:
                    resp = await client.post(url, headers=headers, json=body)

                resp.raise_for_status()

                # Try to parse as JSON
                try:
                    data = resp.json()
                except Exception:
                    # Not JSON, return raw text
                    text = resp.text[:5000]
                    self._success_count += 1
                    return {"success": True, "result": text, "error": None}

                # Apply extract_path if specified
                if extract_path:
                    extracted = _extract_json_path(data, extract_path)
                    if extracted is _MISSING:
                        self._success_count += 1
                        return {
                            "success": True,
                            "result": json.dumps(data, ensure_ascii=False, default=str)[:3000],
                            "error": f"Path '{extract_path}' not found in response",
                        }
                    result_str = json.dumps(extracted, ensure_ascii=False, default=str) \
                        if not isinstance(extracted, str) else extracted
                else:
                    result_str = json.dumps(data, ensure_ascii=False, default=str)[:5000]

                self._success_count += 1
                return {"success": True, "result": result_str, "error": None}

        except httpx.TimeoutException:
            return {"success": False, "result": "",
                    "error": f"Timeout after {self._timeout}s"}
        except httpx.HTTPStatusError as e:
            return {"success": False, "result": "",
                    "error": f"HTTP {e.response.status_code}: {e.response.text[:500]}"}
        except Exception as e:
            log.warning("GenericAPISkill failed for %s: %s", url, e)
            return {"success": False, "result": "", "error": str(e)}

    @property
    def usage_stats(self) -> dict:
        return {
            "call_count": self._call_count,
            "success_count": self._success_count,
        }


class _MissingSentinel:
    pass


_MISSING = _MissingSentinel()


def _extract_json_path(data: Any, path: str) -> Any:
    """Extract a value from nested dict/list using dot-separated path.

    Supports dict keys and list indices: "data.results.0.price"
    Returns _MISSING sentinel if path not found.
    """
    current = data
    for key in path.split("."):
        if isinstance(current, dict):
            if key in current:
                current = current[key]
            else:
                return _MISSING
        elif isinstance(current, (list, tuple)):
            try:
                idx = int(key)
                current = current[idx]
            except (ValueError, IndexError):
                return _MISSING
        else:
            return _MISSING
    return current
