"""TranslateSkill — translate text using the LLM client."""

from __future__ import annotations

import logging
from typing import Any, Optional

from .base import Skill, SkillResult

log = logging.getLogger(__name__)


class TranslateSkill(Skill):
    """Translate text between languages using the LLM backend."""

    def __init__(self, llm_client: Optional[Any] = None) -> None:
        super().__init__()
        self._llm_client = llm_client
        self._call_count = 0
        self._success_count = 0

    @property
    def name(self) -> str:
        return "translate"

    @property
    def description(self) -> str:
        return "使用LLM翻译文本，支持任意语言对"

    @property
    def capabilities(self) -> list[str]:
        return ["translate_text", "language_conversion", "localize"]

    @property
    def param_schema(self) -> dict:
        return {
            "text": {"type": "str", "required": True,
                     "description": "Text to translate"},
            "target_language": {"type": "str", "required": False,
                                "default": "English",
                                "description": "Target language (e.g. 'English', 'Chinese', 'Japanese')"},
            "source_language": {"type": "str", "required": False,
                                "description": "Source language (auto-detect if omitted)"},
        }

    @property
    def risk_level(self) -> str:
        return "safe"

    async def execute(self, params: dict) -> dict:
        text = params.get("text", "")
        target_language = params.get("target_language", "English")
        source_language = params.get("source_language", "")
        self._call_count += 1

        if not text.strip():
            return {"success": False, "result": "", "error": "No text provided"}

        if self._llm_client is None:
            return {"success": False, "result": "",
                    "error": "LLM client not configured. TranslateSkill requires an llm_client."}

        # Build translation prompt
        if source_language:
            prompt = (
                f"Translate the following text from {source_language} to {target_language}. "
                f"Return ONLY the translated text, nothing else.\n\n"
                f"Text:\n{text}"
            )
        else:
            prompt = (
                f"Translate the following text to {target_language}. "
                f"Auto-detect the source language. "
                f"Return ONLY the translated text, nothing else.\n\n"
                f"Text:\n{text}"
            )

        try:
            response = await self._llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
            )
            translated = response.get("content", "").strip()

            if not translated:
                return {"success": False, "result": "",
                        "error": "LLM returned empty translation"}

            self._success_count += 1
            return {
                "success": True,
                "result": translated,
                "error": None,
            }

        except Exception as e:
            log.error("TranslateSkill failed: %s", e)
            return {"success": False, "result": "", "error": str(e)}

    @property
    def usage_stats(self) -> dict:
        return {
            "call_count": self._call_count,
            "success_count": self._success_count,
        }
