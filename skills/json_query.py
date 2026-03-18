"""JSONQuerySkill — query, filter, and transform JSON data."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from .base import Skill, SkillResult

log = logging.getLogger(__name__)


def _extract_path(data: Any, path: str) -> Any:
    """Navigate nested dicts/lists using dot notation with array indices.

    Examples:
        "users[0].name"  -> data["users"][0]["name"]
        "data.items.2"   -> data["data"]["items"][2]
    """
    # Expand bracket notation: "users[0]" -> "users.0"
    path = re.sub(r"\[(\d+)\]", r".\1", path)

    current = data
    for key in path.split("."):
        if not key:
            continue
        if isinstance(current, dict):
            if key in current:
                current = current[key]
            else:
                raise KeyError(f"Key '{key}' not found")
        elif isinstance(current, (list, tuple)):
            try:
                idx = int(key)
                current = current[idx]
            except (ValueError, IndexError) as e:
                raise KeyError(f"Invalid index '{key}': {e}")
        else:
            raise KeyError(f"Cannot navigate into {type(current).__name__} with key '{key}'")
    return current


def _apply_filter(items: list, filter_expr: str) -> list:
    """Apply a simple filter expression to a list of dicts.

    Supported operators: >, <, >=, <=, ==, !=, contains
    Examples:
        "price > 100"
        "name == 'Alice'"
        "tags contains 'python'"
    """
    if not items:
        return []

    # Parse the filter expression
    match = re.match(
        r"^\s*(\w+)\s+(>|<|>=|<=|==|!=|contains)\s+(.+?)\s*$",
        filter_expr,
    )
    if not match:
        raise ValueError(f"Invalid filter expression: {filter_expr}")

    field, op, value_str = match.groups()

    # Try to parse value
    value_str = value_str.strip("'\"")
    try:
        value: Any = float(value_str)
    except ValueError:
        value = value_str

    result: list = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_val = item.get(field)
        if item_val is None:
            continue

        try:
            if op == ">" and float(item_val) > float(value):
                result.append(item)
            elif op == "<" and float(item_val) < float(value):
                result.append(item)
            elif op == ">=" and float(item_val) >= float(value):
                result.append(item)
            elif op == "<=" and float(item_val) <= float(value):
                result.append(item)
            elif op == "==" and str(item_val) == str(value):
                result.append(item)
            elif op == "!=" and str(item_val) != str(value):
                result.append(item)
            elif op == "contains":
                if isinstance(item_val, (list, tuple)):
                    if value in item_val:
                        result.append(item)
                elif isinstance(item_val, str) and str(value) in item_val:
                    result.append(item)
        except (ValueError, TypeError):
            continue

    return result


def _aggregate(items: list, func_name: str, field: str = "") -> Any:
    """Simple aggregations: count, sum, avg."""
    if func_name == "count":
        return len(items)

    if not field:
        raise ValueError(f"Aggregation '{func_name}' requires a field name")

    values = []
    for item in items:
        if isinstance(item, dict) and field in item:
            try:
                values.append(float(item[field]))
            except (ValueError, TypeError):
                continue

    if not values:
        return 0

    if func_name == "sum":
        return sum(values)
    elif func_name == "avg":
        return sum(values) / len(values)
    else:
        raise ValueError(f"Unknown aggregation: {func_name}")


def _format_as_table(items: list) -> str:
    """Format a list of dicts as a simple text table."""
    if not items:
        return "(empty)"
    if not isinstance(items[0], dict):
        return "\n".join(str(item) for item in items)

    # Collect all keys
    keys = list(dict.fromkeys(k for item in items for k in item))
    # Calculate column widths
    widths = {k: max(len(str(k)), max((len(str(item.get(k, ""))) for item in items), default=0))
              for k in keys}

    # Header
    header = " | ".join(str(k).ljust(widths[k]) for k in keys)
    separator = "-+-".join("-" * widths[k] for k in keys)
    rows = [
        " | ".join(str(item.get(k, "")).ljust(widths[k]) for k in keys)
        for item in items
    ]

    return "\n".join([header, separator] + rows)


class JSONQuerySkill(Skill):
    """Query, filter, and transform JSON data. Like jq but in Python."""

    def __init__(self) -> None:
        super().__init__()
        self._call_count = 0
        self._success_count = 0

    @property
    def name(self) -> str:
        return "json_query"

    @property
    def description(self) -> str:
        return "查询/过滤/转换JSON数据，支持路径访问、数组过滤、聚合操作"

    @property
    def capabilities(self) -> list[str]:
        return ["query_json", "filter_data", "transform_json", "extract_fields"]

    @property
    def param_schema(self) -> dict:
        return {
            "data": {"type": "str", "required": True,
                     "description": "JSON string or path to a .json file"},
            "query": {"type": "str", "required": False,
                      "description": "Dot-notation path, e.g. 'data.users[0].name'"},
            "filter": {"type": "str", "required": False,
                       "description": "Filter expression, e.g. 'price > 100'"},
            "aggregate": {"type": "str", "required": False,
                          "description": "Aggregation: 'count', 'sum:field', 'avg:field'"},
            "output_format": {"type": "str", "required": False,
                              "default": "json",
                              "description": "'json', 'text', or 'table'"},
        }

    @property
    def risk_level(self) -> str:
        return "safe"

    async def execute(self, params: dict) -> dict:
        data_str = params.get("data", "")
        query = params.get("query", "")
        filter_expr = params.get("filter", "")
        aggregate_expr = params.get("aggregate", "")
        output_format = params.get("output_format", "json")
        self._call_count += 1

        if not data_str:
            return {"success": False, "result": "", "error": "No data provided"}

        # Load data
        try:
            data = self._load_data(data_str)
        except Exception as e:
            return {"success": False, "result": "", "error": f"Failed to parse data: {e}"}

        try:
            # Apply query path
            result = data
            if query:
                result = _extract_path(result, query)

            # Apply filter (only on lists)
            if filter_expr:
                if not isinstance(result, list):
                    return {"success": False, "result": "",
                            "error": "Filter requires data to be an array"}
                result = _apply_filter(result, filter_expr)

            # Apply aggregation
            if aggregate_expr:
                if not isinstance(result, list):
                    return {"success": False, "result": "",
                            "error": "Aggregation requires data to be an array"}
                if ":" in aggregate_expr:
                    func, field = aggregate_expr.split(":", 1)
                else:
                    func, field = aggregate_expr, ""
                result = _aggregate(result, func.strip(), field.strip())

            # Format output
            output = self._format_output(result, output_format)

            self._success_count += 1
            return {"success": True, "result": output, "error": None}

        except Exception as e:
            log.error("JSONQuerySkill failed: %s", e)
            return {"success": False, "result": "", "error": str(e)}

    @staticmethod
    def _load_data(data_str: str) -> Any:
        """Load JSON from string or file path."""
        # Try as file path first
        path = Path(data_str.strip())
        if path.exists() and path.suffix.lower() == ".json":
            return json.loads(path.read_text(encoding="utf-8"))

        # Parse as JSON string
        return json.loads(data_str)

    @staticmethod
    def _format_output(data: Any, fmt: str) -> str:
        if fmt == "table" and isinstance(data, list):
            return _format_as_table(data)
        elif fmt == "text":
            if isinstance(data, list):
                return "\n".join(str(item) for item in data)
            return str(data)
        else:
            return json.dumps(data, ensure_ascii=False, indent=2, default=str)

    @property
    def usage_stats(self) -> dict:
        return {
            "call_count": self._call_count,
            "success_count": self._success_count,
        }
