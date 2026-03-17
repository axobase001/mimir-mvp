"""CodeExecSkill — execute Python code via subprocess with timeout."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

from .base import Skill, SkillResult

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30


class CodeExecSkill(Skill):
    """Execute Python code in a subprocess with timeout."""

    def __init__(self, timeout: int = _DEFAULT_TIMEOUT) -> None:
        super().__init__()
        self._timeout = timeout
        self._call_count = 0
        self._success_count = 0

    @property
    def name(self) -> str:
        return "code_exec"

    @property
    def description(self) -> str:
        return "执行Python代码，返回stdout/stderr"

    @property
    def capabilities(self) -> list[str]:
        return ["run_code", "data_processing", "calculation", "automation"]

    @property
    def param_schema(self) -> dict:
        return {
            "code": {"type": "str", "required": True, "description": "Python code to execute"},
            "timeout": {"type": "int", "required": False, "default": _DEFAULT_TIMEOUT, "description": "Timeout in seconds"},
        }

    @property
    def risk_level(self) -> str:
        return "dangerous"

    async def execute(self, params: dict) -> dict:
        code = params.get("code", "")
        timeout = params.get("timeout", self._timeout)
        self._call_count += 1

        if not code.strip():
            return {"success": False, "result": "", "error": "Empty code"}

        log.warning("CodeExecSkill: executing user code (timeout=%ds)", timeout)

        try:
            # Write code to a temp file and execute
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, encoding="utf-8"
            ) as f:
                f.write(code)
                tmp_path = f.name

            try:
                proc = await asyncio.wait_for(
                    asyncio.create_subprocess_exec(
                        sys.executable, tmp_path,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    ),
                    timeout=5,  # timeout for starting process
                )
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout,
                )
            finally:
                Path(tmp_path).unlink(missing_ok=True)

            stdout_str = stdout.decode("utf-8", errors="replace").strip()
            stderr_str = stderr.decode("utf-8", errors="replace").strip()

            if proc.returncode == 0:
                self._success_count += 1
                result_text = stdout_str
                if stderr_str:
                    result_text += f"\n[stderr]: {stderr_str}"
                return {"success": True, "result": result_text, "error": None}
            else:
                error_msg = stderr_str or f"Process exited with code {proc.returncode}"
                return {"success": False, "result": stdout_str, "error": error_msg}

        except asyncio.TimeoutError:
            return {"success": False, "result": "", "error": f"Execution timed out after {timeout}s"}
        except Exception as e:
            log.error("CodeExecSkill failed: %s", e)
            return {"success": False, "result": "", "error": str(e)}

    @property
    def usage_stats(self) -> dict:
        return {
            "call_count": self._call_count,
            "success_count": self._success_count,
        }
