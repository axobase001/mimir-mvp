import logging
from pathlib import Path

from .base import Skill, SkillResult

log = logging.getLogger(__name__)

_MAX_FILE_SIZE = 1_048_576  # 1 MB


class FileReadSkill(Skill):
    def __init__(self) -> None:
        super().__init__()

    @property
    def name(self) -> str:
        return "file_read"

    @property
    def description(self) -> str:
        return "读取本地文件内容"

    @property
    def capabilities(self) -> list[str]:
        return ["read_file", "inspect_content"]

    @property
    def param_schema(self) -> dict:
        return {
            "path": {"type": "str", "required": True, "description": "File path to read"},
        }

    @property
    def risk_level(self) -> str:
        return "safe"

    async def execute(self, params: dict) -> dict:
        path_str = params.get("path", "")
        try:
            p = Path(path_str)
            if not p.exists():
                return {"success": False, "result": "", "error": f"File not found: {path_str}"}
            if p.stat().st_size > _MAX_FILE_SIZE:
                return {"success": False, "result": "", "error": "File exceeds 1MB limit"}
            content = p.read_text(encoding="utf-8")
            return {"success": True, "result": content, "error": None}
        except Exception as e:
            return {"success": False, "result": "", "error": str(e)}


class FileWriteSkill(Skill):
    def __init__(self) -> None:
        super().__init__()

    @property
    def name(self) -> str:
        return "file_write"

    @property
    def description(self) -> str:
        return "创建或写入本地文件"

    @property
    def capabilities(self) -> list[str]:
        return ["write_file", "create_file"]

    @property
    def param_schema(self) -> dict:
        return {
            "path": {"type": "str", "required": True, "description": "File path to write"},
            "content": {"type": "str", "required": True, "description": "Content to write"},
            "mode": {"type": "str", "required": False, "default": "w", "description": "'w' or 'a'"},
        }

    @property
    def risk_level(self) -> str:
        return "review"

    async def execute(self, params: dict) -> dict:
        path_str = params.get("path", "")
        content = params.get("content", "")
        mode = params.get("mode", "w")
        if mode not in ("w", "a"):
            mode = "w"
        try:
            p = Path(path_str)
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open(mode, encoding="utf-8") as f:
                f.write(content)
            return {"success": True, "result": f"Written to {path_str}", "error": None}
        except Exception as e:
            return {"success": False, "result": "", "error": str(e)}
