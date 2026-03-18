"""ShellExecSkill — execute arbitrary shell commands via subprocess."""

from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import Optional

from .base import Skill, SkillResult

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30

# Hard-coded blacklist of dangerous commands / patterns
_DANGEROUS_PATTERNS: list[str] = [
    "rm -rf /",
    "rm -rf /*",
    "mkfs.",
    "format c:",
    "format d:",
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
    "init 0",
    "init 6",
    "dd if=/dev/zero",
    "dd if=/dev/random",
    ":(){:|:&};:",          # fork bomb
    "chmod -R 777 /",
    "chown -R",
    "wget -O- | sh",
    "curl | sh",
    "curl | bash",
    "> /dev/sda",
    "mv /* /dev/null",
    "echo '' > /etc/passwd",
    "echo '' > /etc/shadow",
]


def _is_dangerous(command: str) -> Optional[str]:
    """Check if a command matches the blacklist. Returns the matched pattern or None."""
    cmd_lower = command.lower().strip()
    for pattern in _DANGEROUS_PATTERNS:
        if pattern.lower() in cmd_lower:
            return pattern
    return None


class ShellExecSkill(Skill):
    """Execute arbitrary shell commands with timeout and safety checks."""

    def __init__(self, timeout: int = _DEFAULT_TIMEOUT) -> None:
        super().__init__()
        self._timeout = timeout
        self._call_count = 0
        self._success_count = 0

    @property
    def name(self) -> str:
        return "shell_exec"

    @property
    def description(self) -> str:
        return "执行任意shell命令，捕获stdout/stderr，支持timeout"

    @property
    def capabilities(self) -> list[str]:
        return ["run_command", "system_admin", "deployment", "file_management"]

    @property
    def param_schema(self) -> dict:
        return {
            "command": {"type": "str", "required": True,
                        "description": "Shell command to execute"},
            "timeout": {"type": "int", "required": False,
                        "default": _DEFAULT_TIMEOUT,
                        "description": "Timeout in seconds"},
            "cwd": {"type": "str", "required": False,
                     "description": "Working directory for the command"},
        }

    @property
    def risk_level(self) -> str:
        return "dangerous"

    async def execute(self, params: dict) -> dict:
        command = params.get("command", "")
        timeout = params.get("timeout", self._timeout)
        cwd = params.get("cwd", None)
        self._call_count += 1

        if not command.strip():
            return {"success": False, "result": "", "error": "Empty command"}

        # Safety check
        matched = _is_dangerous(command)
        if matched is not None:
            log.warning("ShellExecSkill: BLOCKED dangerous command matching '%s': %s",
                        matched, command)
            return {
                "success": False,
                "result": "",
                "error": f"Command blocked: matches dangerous pattern '{matched}'",
            }

        log.warning("ShellExecSkill: executing '%s' (timeout=%ds, cwd=%s)",
                     command, timeout, cwd)

        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_shell(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=cwd,
                ),
                timeout=5,  # timeout for starting process
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )

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
                return {
                    "success": False,
                    "result": stdout_str,
                    "error": error_msg,
                }

        except asyncio.TimeoutError:
            return {"success": False, "result": "",
                    "error": f"Command timed out after {timeout}s"}
        except Exception as e:
            log.error("ShellExecSkill failed: %s", e)
            return {"success": False, "result": "", "error": str(e)}

    @property
    def usage_stats(self) -> dict:
        return {
            "call_count": self._call_count,
            "success_count": self._success_count,
        }
