"""DocumentSkill — create/append/edit markdown or text documents."""

from __future__ import annotations

import logging
from pathlib import Path

from .base import Skill, SkillResult

log = logging.getLogger(__name__)

_DEFAULT_WORKSPACE = "."


class DocumentSkill(Skill):
    """Create, append, or edit markdown/text documents in a workspace."""

    def __init__(self, workspace: str = _DEFAULT_WORKSPACE) -> None:
        super().__init__()
        self._workspace = Path(workspace)
        self._call_count = 0
        self._success_count = 0

    @property
    def name(self) -> str:
        return "document"

    @property
    def description(self) -> str:
        return "创建/追加/编辑markdown或txt文档"

    @property
    def capabilities(self) -> list[str]:
        return ["write_document", "generate_report", "format_text", "summarize"]

    @property
    def param_schema(self) -> dict:
        return {
            "action": {
                "type": "str",
                "required": True,
                "description": "'create', 'append', or 'edit'",
            },
            "filename": {"type": "str", "required": True, "description": "File name (e.g., report.md)"},
            "content": {"type": "str", "required": True, "description": "Content to write"},
            "title": {"type": "str", "required": False, "description": "Document title (for create)"},
        }

    @property
    def risk_level(self) -> str:
        return "review"

    async def execute(self, params: dict) -> dict:
        action = params.get("action", "create")
        filename = params.get("filename", "")
        content = params.get("content", "")
        title = params.get("title", "")
        self._call_count += 1

        if not filename:
            return {"success": False, "result": "", "error": "No filename provided"}

        # Ensure file is within workspace
        target = self._workspace / filename
        try:
            target.resolve().relative_to(self._workspace.resolve())
        except ValueError:
            return {"success": False, "result": "", "error": "Path escapes workspace"}

        try:
            target.parent.mkdir(parents=True, exist_ok=True)

            if action == "create":
                text = ""
                if title:
                    text = f"# {title}\n\n"
                text += content
                target.write_text(text, encoding="utf-8")
                self._success_count += 1
                return {
                    "success": True,
                    "result": f"Created {filename} ({len(text)} chars)",
                    "error": None,
                    "artifacts": [str(target)],
                }

            elif action == "append":
                with target.open("a", encoding="utf-8") as f:
                    f.write("\n" + content)
                self._success_count += 1
                return {
                    "success": True,
                    "result": f"Appended to {filename} ({len(content)} chars)",
                    "error": None,
                    "artifacts": [str(target)],
                }

            elif action == "edit":
                if not target.exists():
                    return {"success": False, "result": "", "error": f"File not found: {filename}"}
                # Simple replace: content should be JSON with 'find' and 'replace'
                # Or just overwrite entirely
                target.write_text(content, encoding="utf-8")
                self._success_count += 1
                return {
                    "success": True,
                    "result": f"Edited {filename} ({len(content)} chars)",
                    "error": None,
                    "artifacts": [str(target)],
                }

            else:
                return {"success": False, "result": "", "error": f"Unknown action: {action}"}

        except Exception as e:
            log.error("DocumentSkill failed: %s", e)
            return {"success": False, "result": "", "error": str(e)}

    @property
    def usage_stats(self) -> dict:
        return {
            "call_count": self._call_count,
            "success_count": self._success_count,
        }
