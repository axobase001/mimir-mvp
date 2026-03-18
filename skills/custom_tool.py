"""CustomToolManager — user-defined tools loaded from JSON definitions."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx

from .base import Skill, SkillResult

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 15


@dataclass
class CustomToolDefinition:
    """Parsed definition of a user-created custom tool."""

    name: str
    description: str
    capabilities: list[str]
    risk_level: str  # "safe" | "review" | "dangerous"
    tool_type: str  # "api" | "shell" | "transform"
    config: dict  # tool-specific configuration

    def validate(self) -> Optional[str]:
        """Return an error message if the definition is invalid, else None."""
        if not self.name or not re.match(r"^[a-z][a-z0-9_]{1,48}$", self.name):
            return "name must be lowercase alphanumeric+underscore, 2-49 chars"
        if self.tool_type not in ("api", "shell", "transform"):
            return f"tool_type must be 'api', 'shell', or 'transform', got '{self.tool_type}'"
        if self.risk_level not in ("safe", "review", "dangerous"):
            return f"risk_level must be 'safe', 'review', or 'dangerous', got '{self.risk_level}'"
        # Shell types are always dangerous
        if self.tool_type == "shell" and self.risk_level != "dangerous":
            self.risk_level = "dangerous"
        if not self.config:
            return "config must not be empty"
        return None


class _CustomSkillWrapper(Skill):
    """Runtime wrapper that turns a CustomToolDefinition into a Skill."""

    def __init__(self, definition: CustomToolDefinition) -> None:
        super().__init__()
        self._def = definition
        self._call_count = 0
        self._success_count = 0

    @property
    def name(self) -> str:
        return f"custom:{self._def.name}"

    @property
    def description(self) -> str:
        return self._def.description

    @property
    def capabilities(self) -> list[str]:
        return self._def.capabilities

    @property
    def risk_level(self) -> str:
        return self._def.risk_level

    @property
    def param_schema(self) -> dict:
        if self._def.tool_type == "api":
            return {
                "params": {"type": "dict", "required": False,
                           "description": "Query params or body fields to merge into request"},
            }
        elif self._def.tool_type == "shell":
            return {
                "args": {"type": "str", "required": False,
                         "description": "Arguments to append to command template"},
            }
        elif self._def.tool_type == "transform":
            return {
                "input_data": {"type": "str", "required": True,
                               "description": "Input data (JSON string)"},
            }
        return {}

    async def execute(self, params: dict) -> dict:
        self._call_count += 1
        timeout = self._def.config.get("timeout", _DEFAULT_TIMEOUT)

        try:
            if self._def.tool_type == "api":
                return await self._execute_api(params, timeout)
            elif self._def.tool_type == "shell":
                return await self._execute_shell(params, timeout)
            elif self._def.tool_type == "transform":
                return await self._execute_transform(params)
            else:
                return {"success": False, "result": "",
                        "error": f"Unknown tool_type: {self._def.tool_type}"}
        except Exception as e:
            log.error("Custom tool '%s' failed: %s", self._def.name, e)
            return {"success": False, "result": "", "error": str(e)}

    async def _execute_api(self, params: dict, timeout: int) -> dict:
        """Execute an API-type custom tool."""
        cfg = self._def.config
        url = cfg.get("url", "")
        method = cfg.get("method", "GET").upper()
        headers = cfg.get("headers", {})
        body_template = cfg.get("body_template", {})
        extract_path = cfg.get("extract_path", "")

        if not url:
            return {"success": False, "result": "", "error": "No URL in tool config"}

        # Merge user params into body/query
        user_params = params.get("params", {})

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            if method == "GET":
                resp = await client.get(url, params=user_params, headers=headers)
            elif method == "POST":
                body = {**body_template, **user_params}
                resp = await client.post(url, json=body, headers=headers)
            else:
                return {"success": False, "result": "",
                        "error": f"Unsupported method: {method}"}

            resp.raise_for_status()

            try:
                data = resp.json()
            except Exception:
                self._success_count += 1
                return {"success": True, "result": resp.text[:5000], "error": None}

            if extract_path:
                extracted = self._extract_json_path(data, extract_path)
                if extracted is not None:
                    result_str = json.dumps(extracted, ensure_ascii=False, default=str) \
                        if not isinstance(extracted, str) else extracted
                else:
                    result_str = json.dumps(data, ensure_ascii=False, default=str)[:5000]
            else:
                result_str = json.dumps(data, ensure_ascii=False, default=str)[:5000]

            self._success_count += 1
            return {"success": True, "result": result_str, "error": None}

    async def _execute_shell(self, params: dict, timeout: int) -> dict:
        """Execute a shell-type custom tool."""
        import asyncio
        import subprocess

        cfg = self._def.config
        command_template = cfg.get("command_template", "")
        if not command_template:
            return {"success": False, "result": "", "error": "No command_template in tool config"}

        args = params.get("args", "")
        command = f"{command_template} {args}".strip()

        # Import the blacklist from shell_exec
        from .shell_exec import _is_dangerous
        matched = _is_dangerous(command)
        if matched is not None:
            return {"success": False, "result": "",
                    "error": f"Command blocked: matches dangerous pattern '{matched}'"}

        proc = await asyncio.wait_for(
            asyncio.create_subprocess_shell(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ),
            timeout=5,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

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

    async def _execute_transform(self, params: dict) -> dict:
        """Execute a transform-type custom tool."""
        cfg = self._def.config
        input_data = params.get("input_data", "")
        if not input_data:
            return {"success": False, "result": "", "error": "No input_data provided"}

        try:
            data = json.loads(input_data)
        except json.JSONDecodeError as e:
            return {"success": False, "result": "", "error": f"Invalid JSON input: {e}"}

        # Apply jq-like expression (simple dot-path extraction)
        jq_expr = cfg.get("jq_expression", "")
        if jq_expr:
            extracted = self._extract_json_path(data, jq_expr.lstrip("."))
            if extracted is not None:
                data = extracted

        output_format = cfg.get("output_format", "json")
        if output_format == "text":
            if isinstance(data, list):
                result = "\n".join(str(item) for item in data)
            else:
                result = str(data)
        else:
            result = json.dumps(data, ensure_ascii=False, indent=2, default=str)

        self._success_count += 1
        return {"success": True, "result": result, "error": None}

    @staticmethod
    def _extract_json_path(data: Any, path: str) -> Any:
        """Navigate nested data by dot-separated path. Returns None on failure."""
        current = data
        for key in path.split("."):
            if not key:
                continue
            if isinstance(current, dict):
                if key in current:
                    current = current[key]
                else:
                    return None
            elif isinstance(current, (list, tuple)):
                try:
                    current = current[int(key)]
                except (ValueError, IndexError):
                    return None
            else:
                return None
        return current

    @property
    def usage_stats(self) -> dict:
        return {
            "call_count": self._call_count,
            "success_count": self._success_count,
        }


class CustomToolManager:
    """Manages user-defined custom tools stored as JSON files."""

    def __init__(self, tools_dir: str = "data/custom_tools") -> None:
        self._tools_dir = Path(tools_dir)
        self._tools_dir.mkdir(parents=True, exist_ok=True)
        self._loaded: dict[str, _CustomSkillWrapper] = {}

    def load_tools(self) -> list[Skill]:
        """Load all .json definitions from tools_dir and return as Skill objects."""
        skills: list[Skill] = []
        for json_path in sorted(self._tools_dir.glob("*.json")):
            try:
                raw = json.loads(json_path.read_text(encoding="utf-8"))
                defn = CustomToolDefinition(
                    name=raw["name"],
                    description=raw.get("description", ""),
                    capabilities=raw.get("capabilities", []),
                    risk_level=raw.get("risk_level", "review"),
                    tool_type=raw.get("tool_type", "api"),
                    config=raw.get("config", {}),
                )
                err = defn.validate()
                if err:
                    log.warning("Skipping invalid custom tool '%s': %s", json_path.name, err)
                    continue

                wrapper = _CustomSkillWrapper(defn)
                self._loaded[defn.name] = wrapper
                skills.append(wrapper)
                log.info("Loaded custom tool: %s (type=%s, risk=%s)",
                         defn.name, defn.tool_type, defn.risk_level)

            except Exception as e:
                log.warning("Failed to load custom tool from %s: %s", json_path, e)

        return skills

    def register_tool(self, definition: dict) -> str:
        """Save a new custom tool definition. Returns tool name or raises ValueError."""
        defn = CustomToolDefinition(
            name=definition.get("name", ""),
            description=definition.get("description", ""),
            capabilities=definition.get("capabilities", []),
            risk_level=definition.get("risk_level", "review"),
            tool_type=definition.get("tool_type", "api"),
            config=definition.get("config", {}),
        )
        err = defn.validate()
        if err:
            raise ValueError(f"Invalid tool definition: {err}")

        # Save to disk
        file_path = self._tools_dir / f"{defn.name}.json"
        file_path.write_text(
            json.dumps(definition, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # Create wrapper
        wrapper = _CustomSkillWrapper(defn)
        self._loaded[defn.name] = wrapper

        log.info("Registered custom tool: %s", defn.name)
        return defn.name

    def remove_tool(self, name: str) -> bool:
        """Remove a custom tool by name. Returns True if removed."""
        file_path = self._tools_dir / f"{name}.json"
        if file_path.exists():
            file_path.unlink()
        if name in self._loaded:
            del self._loaded[name]
            log.info("Removed custom tool: %s", name)
            return True
        return False

    def list_tools(self) -> list[dict]:
        """List all loaded custom tools."""
        result: list[dict] = []
        for name, wrapper in self._loaded.items():
            result.append({
                "name": wrapper.name,
                "description": wrapper.description,
                "capabilities": wrapper.capabilities,
                "risk_level": wrapper.risk_level,
                "tool_type": wrapper._def.tool_type,
            })
        return result

    def get_skill(self, name: str) -> Optional[Skill]:
        """Get a loaded custom tool Skill by its short name."""
        return self._loaded.get(name)
