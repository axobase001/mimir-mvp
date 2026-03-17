"""OpenClaw Adapter — scan and wrap external OpenClaw skills into Mimir."""

from __future__ import annotations

import importlib
import importlib.util
import logging
from pathlib import Path
from typing import Any, Optional

from .base import Skill, SkillResult

log = logging.getLogger(__name__)


class WrappedOpenClawSkill(Skill):
    """Wraps an external OpenClaw skill module into Mimir's Skill interface."""

    def __init__(
        self,
        skill_name: str,
        skill_description: str,
        skill_capabilities: list[str],
        skill_risk: str,
        skill_param_schema: dict,
        execute_fn: Any,
    ) -> None:
        super().__init__()
        self._name = skill_name
        self._description = skill_description
        self._capabilities = skill_capabilities
        self._risk_level = skill_risk
        self._param_schema = skill_param_schema
        self._execute_fn = execute_fn

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def capabilities(self) -> list[str]:
        return self._capabilities

    @property
    def param_schema(self) -> dict:
        return self._param_schema

    @property
    def risk_level(self) -> str:
        return self._risk_level

    async def execute(self, params: dict) -> dict:
        try:
            result = self._execute_fn(params)
            # If the external function is async, await it
            if hasattr(result, "__await__"):
                result = await result

            # Normalize to dict
            if isinstance(result, dict):
                return result
            return {"success": True, "result": str(result), "error": None}
        except Exception as e:
            log.error("OpenClaw skill '%s' failed: %s", self._name, e)
            return {"success": False, "result": "", "error": str(e)}


class OpenClawAdapter:
    """Scan OpenClaw skill directories and dynamically wrap them as Mimir Skills."""

    def __init__(self, skill_dirs: list[str] | None = None) -> None:
        self._skill_dirs: list[Path] = []
        if skill_dirs:
            self._skill_dirs = [Path(d) for d in skill_dirs]
        self._loaded: list[Skill] = []

    def load_skills(self) -> list[Skill]:
        """Scan configured directories and load OpenClaw skill modules."""
        self._loaded.clear()

        for skill_dir in self._skill_dirs:
            if not skill_dir.is_dir():
                log.warning("OpenClaw skill dir not found: %s", skill_dir)
                continue

            for py_file in skill_dir.glob("*.py"):
                if py_file.name.startswith("_"):
                    continue
                try:
                    skill = self._load_module(py_file)
                    if skill is not None:
                        self._loaded.append(skill)
                        log.info("Loaded OpenClaw skill: %s from %s", skill.name, py_file)
                except Exception as e:
                    log.warning("Failed to load OpenClaw skill from %s: %s", py_file, e)

        return self._loaded

    @staticmethod
    def _load_module(path: Path) -> Optional[Skill]:
        """Load a Python module and extract OpenClaw skill metadata."""
        module_name = f"openclaw_skill_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, str(path))
        if spec is None or spec.loader is None:
            return None

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # Expected attributes in OpenClaw skill modules:
        #   SKILL_NAME: str
        #   SKILL_DESCRIPTION: str
        #   SKILL_CAPABILITIES: list[str]
        #   SKILL_RISK: str (optional, default "safe")
        #   SKILL_PARAMS: dict (optional)
        #   execute(params: dict) -> dict

        skill_name = getattr(module, "SKILL_NAME", None)
        execute_fn = getattr(module, "execute", None)

        if skill_name is None or execute_fn is None:
            log.debug("Skipping %s: missing SKILL_NAME or execute()", path)
            return None

        return WrappedOpenClawSkill(
            skill_name=skill_name,
            skill_description=getattr(module, "SKILL_DESCRIPTION", f"OpenClaw: {skill_name}"),
            skill_capabilities=getattr(module, "SKILL_CAPABILITIES", []),
            skill_risk=getattr(module, "SKILL_RISK", "safe"),
            skill_param_schema=getattr(module, "SKILL_PARAMS", {}),
            execute_fn=execute_fn,
        )

    def register_all(self, registry: Any) -> int:
        """Load skills and register them all with a SkillRegistry.

        Returns number of skills registered.
        """
        if not self._loaded:
            self.load_skills()

        count = 0
        for skill in self._loaded:
            registry.register(skill)
            count += 1

        return count
