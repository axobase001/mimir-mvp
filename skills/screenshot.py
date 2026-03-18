"""ScreenshotSkill — capture webpage screenshots using Playwright."""

from __future__ import annotations

import logging
from pathlib import Path

from .base import Skill, SkillResult

log = logging.getLogger(__name__)

_DEFAULT_WIDTH = 1280
_DEFAULT_HEIGHT = 720


class ScreenshotSkill(Skill):
    """Capture a screenshot of a URL using Playwright headless browser."""

    def __init__(self) -> None:
        super().__init__()
        self._call_count = 0
        self._success_count = 0

    @property
    def name(self) -> str:
        return "screenshot"

    @property
    def description(self) -> str:
        return "截取URL的网页截图，保存为PNG文件"

    @property
    def capabilities(self) -> list[str]:
        return ["capture_screenshot", "visual_inspect", "webpage_preview"]

    @property
    def param_schema(self) -> dict:
        return {
            "url": {"type": "str", "required": True,
                    "description": "URL to capture"},
            "width": {"type": "int", "required": False,
                      "default": _DEFAULT_WIDTH,
                      "description": "Viewport width in pixels"},
            "height": {"type": "int", "required": False,
                       "default": _DEFAULT_HEIGHT,
                       "description": "Viewport height in pixels"},
            "full_page": {"type": "bool", "required": False,
                          "default": False,
                          "description": "Capture the full scrollable page"},
            "output_path": {"type": "str", "required": True,
                            "description": "File path for the saved screenshot"},
        }

    @property
    def risk_level(self) -> str:
        return "safe"

    async def execute(self, params: dict) -> dict:
        url = params.get("url", "")
        width = params.get("width", _DEFAULT_WIDTH)
        height = params.get("height", _DEFAULT_HEIGHT)
        full_page = params.get("full_page", False)
        output_path = params.get("output_path", "")
        self._call_count += 1

        if not url:
            return {"success": False, "result": "", "error": "No URL provided"}
        if not output_path:
            return {"success": False, "result": "", "error": "No output_path provided"}

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return {
                "success": False,
                "result": "",
                "error": (
                    "Playwright is not installed. "
                    "Install it with: pip install playwright && playwright install chromium"
                ),
            }

        try:
            # Ensure output directory exists
            out = Path(output_path)
            out.parent.mkdir(parents=True, exist_ok=True)

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(
                    viewport={"width": width, "height": height},
                )
                await page.goto(url, wait_until="networkidle", timeout=30000)
                await page.screenshot(path=output_path, full_page=full_page)
                await browser.close()

            self._success_count += 1
            return {
                "success": True,
                "result": f"Screenshot saved to {output_path}",
                "error": None,
                "artifacts": [output_path],
            }

        except Exception as e:
            log.error("ScreenshotSkill failed for %s: %s", url, e)
            return {"success": False, "result": "", "error": str(e)}

    @property
    def usage_stats(self) -> dict:
        return {
            "call_count": self._call_count,
            "success_count": self._success_count,
        }
