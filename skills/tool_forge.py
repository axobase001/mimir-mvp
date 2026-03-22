"""ToolForgeSkill — Skuld creates its own Python tools at runtime.

Actions:
  create  — Write a Python skill file, validate it, hot-register it
  list    — List all forged tools
  remove  — Unregister and delete a forged tool
  pip     — Install a PyPI package into the runtime
  github  — Search GitHub for relevant repos/tools
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import importlib.util
import json
import logging
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any, Optional

import httpx

from .base import Skill

log = logging.getLogger(__name__)

FORGE_DIR = Path("data/forged_tools")
FORGE_DIR.mkdir(parents=True, exist_ok=True)

# Template that Skuld fills in when creating a tool
_TOOL_TEMPLATE = '''\
"""Auto-forged skill: {name}

{description}
"""
import logging
log = logging.getLogger(__name__)

SKILL_NAME = "{name}"
SKILL_DESCRIPTION = """{description}"""
SKILL_CAPABILITIES = {capabilities}
SKILL_RISK = "review"

async def execute(params: dict) -> dict:
    """
    Params: {param_doc}
    Returns: {{"success": bool, "result": str, "error": str|None}}
    """
    try:
{code}
    except Exception as e:
        log.error("Forged tool %s failed: %s", SKILL_NAME, e)
        return {{"success": False, "result": "", "error": str(e)}}
'''


class _ForgedSkillWrapper(Skill):
    """Wraps a forged Python module as a Skill."""

    def __init__(self, name: str, module: Any) -> None:
        super().__init__()
        self._name = name
        self._module = module
        self._call_count = 0

    @property
    def name(self) -> str:
        return f"forged:{self._name}"

    @property
    def description(self) -> str:
        return getattr(self._module, "SKILL_DESCRIPTION", "Forged tool")

    @property
    def capabilities(self) -> list[str]:
        return getattr(self._module, "SKILL_CAPABILITIES", [self._name])

    @property
    def risk_level(self) -> str:
        return getattr(self._module, "SKILL_RISK", "review")

    @property
    def param_schema(self) -> dict:
        return {"params": {"type": "dict", "required": False,
                           "description": "Parameters for the forged tool"}}

    async def execute(self, params: dict) -> dict:
        self._call_count += 1
        fn = getattr(self._module, "execute", None)
        if fn is None:
            return {"success": False, "result": "",
                    "error": "Forged tool has no execute() function"}
        result = await fn(params)
        return result

    @property
    def usage_stats(self) -> dict:
        return {"call_count": self._call_count}


class ToolForgeSkill(Skill):
    """Meta-skill: Skuld creates, manages, and installs its own tools."""

    def __init__(self, registry: Any = None) -> None:
        super().__init__()
        self._registry = registry  # SmartSkillRegistry, set after init
        self._call_count = 0
        self._forged: dict[str, _ForgedSkillWrapper] = {}
        # Auto-load previously forged tools
        self._load_existing()

    def _load_existing(self) -> None:
        """Load all .py files from FORGE_DIR on startup."""
        for py_path in sorted(FORGE_DIR.glob("*.py")):
            try:
                wrapper = self._load_module(py_path)
                if wrapper:
                    self._forged[wrapper._name] = wrapper
                    log.info("Loaded forged tool: %s", wrapper._name)
            except Exception as e:
                log.warning("Failed to load forged tool %s: %s", py_path.name, e)

    def get_forged_skills(self) -> list[Skill]:
        """Return all forged skills for registration."""
        return list(self._forged.values())

    @property
    def name(self) -> str:
        return "tool_forge"

    @property
    def description(self) -> str:
        return ("Create new Python tools at runtime, install pip packages, "
                "or search GitHub for useful libraries. "
                "Actions: create, list, remove, pip, github")

    @property
    def capabilities(self) -> list[str]:
        return ["create_tool", "install_package", "search_github",
                "extend_capabilities", "automation"]

    @property
    def risk_level(self) -> str:
        return "dangerous"

    @property
    def param_schema(self) -> dict:
        return {
            "action": {
                "type": "str", "required": True,
                "description": "create | list | remove | pip | github",
            },
            "name": {
                "type": "str", "required": False,
                "description": "Tool name (for create/remove)",
            },
            "description": {
                "type": "str", "required": False,
                "description": "What the tool does (for create)",
            },
            "capabilities": {
                "type": "list", "required": False,
                "description": "Capability tags (for create)",
            },
            "code": {
                "type": "str", "required": False,
                "description": "Python code for the execute() body (for create). "
                               "Must use params dict and return "
                               '{"success": bool, "result": str, "error": str|None}',
            },
            "package": {
                "type": "str", "required": False,
                "description": "PyPI package name (for pip)",
            },
            "query": {
                "type": "str", "required": False,
                "description": "Search query (for github)",
            },
        }

    async def execute(self, params: dict) -> dict:
        self._call_count += 1
        action = (params.get("action") or "").strip().lower()

        if action == "create":
            return await self._create(params)
        elif action == "list":
            return self._list()
        elif action == "remove":
            return self._remove(params.get("name", ""))
        elif action == "pip":
            return await self._pip_install(params.get("package", ""))
        elif action == "github":
            return await self._github_search(params.get("query", ""))
        else:
            return {"success": False, "result": "",
                    "error": f"Unknown action: {action}. Use: create, list, remove, pip, github"}

    async def _create(self, params: dict) -> dict:
        """Create a new Python tool from code."""
        name = (params.get("name") or "").strip().lower().replace(" ", "_")
        if not name or not name.isidentifier():
            return {"success": False, "result": "",
                    "error": f"Invalid tool name: '{name}'. Must be a valid Python identifier."}

        description = params.get("description", f"Forged tool: {name}")
        capabilities = params.get("capabilities", [name])
        code = params.get("code", "")

        if not code.strip():
            return {"success": False, "result": "",
                    "error": "No code provided. Provide Python code for the execute() body."}

        # Indent code to fit inside the template's try block
        code_lines = code.strip().split("\n")
        indented = "\n".join("        " + line for line in code_lines)

        # Generate full source
        source = _TOOL_TEMPLATE.format(
            name=name,
            description=description,
            capabilities=capabilities,
            param_doc="dict with tool-specific parameters",
            code=indented,
        )

        # Syntax check
        try:
            ast.parse(source)
        except SyntaxError as e:
            return {"success": False, "result": "",
                    "error": f"Syntax error in generated code: {e}"}

        # Write to file
        file_path = FORGE_DIR / f"{name}.py"
        file_path.write_text(source, encoding="utf-8")

        # Load and register
        try:
            wrapper = self._load_module(file_path)
            if wrapper is None:
                return {"success": False, "result": "",
                        "error": "Failed to load forged module"}
            self._forged[name] = wrapper

            # Hot-register into the skill registry if available
            if self._registry is not None:
                self._registry.register(wrapper)

            log.info("Forged new tool: %s", name)
            return {
                "success": True,
                "result": f"Tool '{name}' created and registered. "
                          f"Capabilities: {capabilities}. "
                          f"Use it via skill name 'forged:{name}'.",
                "error": None,
            }
        except Exception as e:
            file_path.unlink(missing_ok=True)
            return {"success": False, "result": "",
                    "error": f"Failed to load tool: {e}"}

    def _list(self) -> dict:
        """List all forged tools."""
        if not self._forged:
            return {"success": True, "result": "No forged tools yet.", "error": None}

        lines = []
        for name, wrapper in self._forged.items():
            lines.append(f"- forged:{name}: {wrapper.description} "
                         f"(caps={wrapper.capabilities})")
        return {"success": True, "result": "\n".join(lines), "error": None}

    def _remove(self, name: str) -> dict:
        """Remove a forged tool."""
        name = name.strip().lower().replace("forged:", "")
        if name not in self._forged:
            return {"success": False, "result": "",
                    "error": f"Tool '{name}' not found in forged tools."}

        file_path = FORGE_DIR / f"{name}.py"
        file_path.unlink(missing_ok=True)
        del self._forged[name]

        log.info("Removed forged tool: %s", name)
        return {"success": True,
                "result": f"Tool '{name}' removed.",
                "error": None}

    async def _pip_install(self, package: str) -> dict:
        """Install a PyPI package."""
        package = package.strip()
        if not package:
            return {"success": False, "result": "",
                    "error": "No package name provided."}

        # Basic safety: no shell injection
        if any(c in package for c in ";|&$`"):
            return {"success": False, "result": "",
                    "error": f"Invalid package name: {package}"}

        log.info("Installing pip package: %s", package)
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip", "install", "--no-input", package,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            out = stdout.decode("utf-8", errors="replace")
            err = stderr.decode("utf-8", errors="replace")

            if proc.returncode == 0:
                return {"success": True,
                        "result": f"Installed {package}.\n{out[-500:]}",
                        "error": None}
            else:
                return {"success": False, "result": out[-500:],
                        "error": err[-500:]}
        except asyncio.TimeoutError:
            return {"success": False, "result": "",
                    "error": "pip install timed out (120s)"}

    async def _github_search(self, query: str) -> dict:
        """Search GitHub for repos matching query."""
        query = query.strip()
        if not query:
            return {"success": False, "result": "",
                    "error": "No search query provided."}

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    "https://api.github.com/search/repositories",
                    params={"q": query, "sort": "stars", "per_page": 5},
                    headers={"Accept": "application/vnd.github.v3+json"},
                )
                resp.raise_for_status()
                data = resp.json()

            items = data.get("items", [])
            if not items:
                return {"success": True,
                        "result": f"No repos found for '{query}'.",
                        "error": None}

            lines = []
            for repo in items:
                lines.append(
                    f"- {repo['full_name']} ({repo['stargazers_count']} stars)\n"
                    f"  {repo.get('description', 'No description')}\n"
                    f"  URL: {repo['html_url']}\n"
                    f"  Language: {repo.get('language', '?')}"
                )
            return {"success": True,
                    "result": "\n".join(lines),
                    "error": None}

        except Exception as e:
            return {"success": False, "result": "",
                    "error": f"GitHub search failed: {e}"}

    def _load_module(self, file_path: Path) -> Optional[_ForgedSkillWrapper]:
        """Dynamically load a Python file as a module."""
        name = file_path.stem
        spec = importlib.util.spec_from_file_location(f"forged_{name}", str(file_path))
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Verify it has an execute function
        if not hasattr(module, "execute"):
            log.warning("Forged tool %s has no execute() function", name)
            return None

        return _ForgedSkillWrapper(name, module)

    @property
    def usage_stats(self) -> dict:
        return {"call_count": self._call_count,
                "forged_tools": len(self._forged)}
