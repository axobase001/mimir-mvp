"""Tests for new skills (v2): shell_exec, screenshot, calendar, slack_webhook,
json_query, translate, summarize_url, and custom_tool framework.

Each skill has at least 3 tests covering properties, success paths, and error paths.
"""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# ══════════════════════════════════════════════════
# ShellExecSkill
# ══════════════════════════════════════════════════

from mimir.skills.shell_exec import ShellExecSkill, _is_dangerous


def test_shell_exec_properties():
    skill = ShellExecSkill()
    assert skill.name == "shell_exec"
    assert "run_command" in skill.capabilities
    assert skill.risk_level == "dangerous"
    assert "command" in skill.param_schema


def test_shell_exec_empty_command():
    skill = ShellExecSkill()
    result = asyncio.run(skill.execute({"command": ""}))
    assert result["success"] is False
    assert "empty" in result["error"].lower()


def test_shell_exec_dangerous_blocked():
    skill = ShellExecSkill()
    result = asyncio.run(skill.execute({"command": "rm -rf /"}))
    assert result["success"] is False
    assert "blocked" in result["error"].lower()


def test_shell_exec_dangerous_format():
    skill = ShellExecSkill()
    result = asyncio.run(skill.execute({"command": "shutdown -h now"}))
    assert result["success"] is False
    assert "blocked" in result["error"].lower()


def test_is_dangerous_util():
    assert _is_dangerous("rm -rf /") is not None
    assert _is_dangerous("rm -rf /*") is not None
    assert _is_dangerous("echo hello") is None
    assert _is_dangerous("ls -la") is None
    assert _is_dangerous("shutdown -h") is not None


def test_shell_exec_echo():
    """Execute a safe command: echo."""
    skill = ShellExecSkill()
    result = asyncio.run(skill.execute({"command": "echo hello_world"}))
    assert result["success"] is True
    assert "hello_world" in result["result"]


def test_shell_exec_timeout():
    """Test that timeout is enforced."""
    skill = ShellExecSkill()
    # Use a command that hangs — 'sleep 60' with 1s timeout
    result = asyncio.run(skill.execute({"command": "sleep 60", "timeout": 1}))
    assert result["success"] is False
    assert "timed out" in result["error"].lower()


# ══════════════════════════════════════════════════
# ScreenshotSkill
# ══════════════════════════════════════════════════

from mimir.skills.screenshot import ScreenshotSkill


def test_screenshot_properties():
    skill = ScreenshotSkill()
    assert skill.name == "screenshot"
    assert "capture_screenshot" in skill.capabilities
    assert skill.risk_level == "safe"
    assert "url" in skill.param_schema


def test_screenshot_no_url():
    skill = ScreenshotSkill()
    result = asyncio.run(skill.execute({"output_path": "/tmp/test.png"}))
    assert result["success"] is False
    assert "no url" in result["error"].lower()


def test_screenshot_no_output_path():
    skill = ScreenshotSkill()
    result = asyncio.run(skill.execute({"url": "http://example.com"}))
    assert result["success"] is False
    assert "no output_path" in result["error"].lower()


def test_screenshot_playwright_not_installed():
    """When playwright is not installed, should return friendly error."""
    skill = ScreenshotSkill()
    with patch.dict("sys.modules", {"playwright": None, "playwright.async_api": None}):
        # Force ImportError by patching the import
        original_import = __builtins__.__import__ if hasattr(__builtins__, '__import__') else __import__

        def mock_import(name, *args, **kwargs):
            if "playwright" in name:
                raise ImportError("No module named 'playwright'")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            result = asyncio.run(skill.execute({
                "url": "http://example.com",
                "output_path": "/tmp/test.png",
            }))
            assert result["success"] is False
            assert "playwright" in result["error"].lower()


# ══════════════════════════════════════════════════
# CalendarSkill
# ══════════════════════════════════════════════════

from mimir.skills.calendar_ical import CalendarSkill, _parse_ics_events, _build_vevent


def test_calendar_properties():
    skill = CalendarSkill()
    assert skill.name == "calendar"
    assert "read_calendar" in skill.capabilities
    assert skill.risk_level == "review"
    assert "action" in skill.param_schema


def test_calendar_no_action():
    skill = CalendarSkill()
    result = asyncio.run(skill.execute({"path": "/tmp/test.ics"}))
    assert result["success"] is False
    assert "no action" in result["error"].lower()


def test_calendar_no_path():
    skill = CalendarSkill()
    result = asyncio.run(skill.execute({"action": "list"}))
    assert result["success"] is False
    assert "no path" in result["error"].lower()


def test_calendar_list_file_not_found():
    skill = CalendarSkill()
    result = asyncio.run(skill.execute({"action": "list", "path": "/nonexistent/cal.ics"}))
    assert result["success"] is False
    assert "not found" in result["error"].lower()


def test_calendar_create_and_list():
    """Create an event in a new .ics file, then list it."""
    skill = CalendarSkill()
    with tempfile.NamedTemporaryFile(suffix=".ics", delete=False) as f:
        ics_path = f.name
    # Remove the file so we test creation from scratch
    Path(ics_path).unlink(missing_ok=True)

    try:
        # Create event
        result = asyncio.run(skill.execute({
            "action": "create",
            "path": ics_path,
            "title": "Team Meeting",
            "start": "2026-03-20T10:00:00",
            "end": "2026-03-20T11:00:00",
            "description": "Weekly sync",
        }))
        assert result["success"] is True
        assert "Team Meeting" in result["result"]

        # List events
        result = asyncio.run(skill.execute({"action": "list", "path": ics_path}))
        assert result["success"] is True
        assert "Team Meeting" in result["result"]

        # Export events
        result = asyncio.run(skill.execute({"action": "export", "path": ics_path}))
        assert result["success"] is True
        assert "Team Meeting" in result["result"]
    finally:
        Path(ics_path).unlink(missing_ok=True)


def test_calendar_create_missing_fields():
    skill = CalendarSkill()
    result = asyncio.run(skill.execute({
        "action": "create",
        "path": "/tmp/test.ics",
        "title": "Test",
        # Missing start/end
    }))
    assert result["success"] is False
    assert "start" in result["error"].lower() or "no start" in result["error"].lower()


def test_parse_ics_events():
    ics_text = (
        "BEGIN:VCALENDAR\r\n"
        "BEGIN:VEVENT\r\n"
        "SUMMARY:Test Event\r\n"
        "DTSTART:20260320T100000Z\r\n"
        "DTEND:20260320T110000Z\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    events = _parse_ics_events(ics_text)
    assert len(events) == 1
    assert events[0]["SUMMARY"] == "Test Event"


def test_build_vevent():
    vevent = _build_vevent("Meeting", "2026-03-20T10:00:00", "2026-03-20T11:00:00")
    assert "SUMMARY:Meeting" in vevent
    assert "BEGIN:VEVENT" in vevent
    assert "END:VEVENT" in vevent


# ══════════════════════════════════════════════════
# SlackWebhookSkill
# ══════════════════════════════════════════════════

from mimir.skills.slack_webhook import SlackWebhookSkill


def test_slack_webhook_properties():
    skill = SlackWebhookSkill()
    assert skill.name == "slack_webhook"
    assert "send_message" in skill.capabilities
    assert skill.risk_level == "review"
    assert "webhook_url" in skill.param_schema


def test_slack_webhook_no_url():
    skill = SlackWebhookSkill()
    result = asyncio.run(skill.execute({"message": "hello"}))
    assert result["success"] is False
    assert "no webhook_url" in result["error"].lower()


def test_slack_webhook_no_message():
    skill = SlackWebhookSkill()
    result = asyncio.run(skill.execute({"webhook_url": "https://hooks.slack.com/test"}))
    assert result["success"] is False
    assert "no message" in result["error"].lower()


def test_slack_webhook_mock_success():
    skill = SlackWebhookSkill()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()

    async def mock_post(*args, **kwargs):
        return mock_resp

    with patch("mimir.skills.slack_webhook.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = asyncio.run(skill.execute({
            "webhook_url": "https://hooks.slack.com/services/T00/B00/xxx",
            "message": "Hello from Skuld!",
            "channel": "#general",
            "username": "skuld-bot",
        }))

    assert result["success"] is True
    assert "sent" in result["result"].lower()


# ══════════════════════════════════════════════════
# JSONQuerySkill
# ══════════════════════════════════════════════════

from mimir.skills.json_query import (
    JSONQuerySkill,
    _extract_path,
    _apply_filter,
    _aggregate,
    _format_as_table,
)


def test_json_query_properties():
    skill = JSONQuerySkill()
    assert skill.name == "json_query"
    assert "query_json" in skill.capabilities
    assert skill.risk_level == "safe"
    assert "data" in skill.param_schema


def test_json_query_no_data():
    skill = JSONQuerySkill()
    result = asyncio.run(skill.execute({}))
    assert result["success"] is False
    assert "no data" in result["error"].lower()


def test_json_query_path_extraction():
    data = json.dumps({"users": [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]})
    skill = JSONQuerySkill()

    result = asyncio.run(skill.execute({
        "data": data,
        "query": "users[0].name",
    }))
    assert result["success"] is True
    assert "Alice" in result["result"]


def test_json_query_filter():
    data = json.dumps([
        {"name": "Widget A", "price": 50},
        {"name": "Widget B", "price": 150},
        {"name": "Widget C", "price": 200},
    ])
    skill = JSONQuerySkill()

    result = asyncio.run(skill.execute({
        "data": data,
        "filter": "price > 100",
        "output_format": "json",
    }))
    assert result["success"] is True
    parsed = json.loads(result["result"])
    assert len(parsed) == 2
    assert all(item["price"] > 100 for item in parsed)


def test_json_query_aggregate_count():
    data = json.dumps([{"x": 1}, {"x": 2}, {"x": 3}])
    skill = JSONQuerySkill()

    result = asyncio.run(skill.execute({
        "data": data,
        "aggregate": "count",
    }))
    assert result["success"] is True
    assert "3" in result["result"]


def test_json_query_aggregate_sum():
    data = json.dumps([{"val": 10}, {"val": 20}, {"val": 30}])
    skill = JSONQuerySkill()

    result = asyncio.run(skill.execute({
        "data": data,
        "aggregate": "sum:val",
    }))
    assert result["success"] is True
    assert "60" in result["result"]


def test_json_query_aggregate_avg():
    data = json.dumps([{"val": 10}, {"val": 20}, {"val": 30}])
    skill = JSONQuerySkill()

    result = asyncio.run(skill.execute({
        "data": data,
        "aggregate": "avg:val",
    }))
    assert result["success"] is True
    # avg = 20.0
    assert "20" in result["result"]


def test_json_query_table_format():
    data = json.dumps([{"name": "A", "score": 10}, {"name": "B", "score": 20}])
    skill = JSONQuerySkill()

    result = asyncio.run(skill.execute({
        "data": data,
        "output_format": "table",
    }))
    assert result["success"] is True
    assert "name" in result["result"]
    assert "score" in result["result"]
    assert "---" in result["result"]


def test_json_query_from_file():
    data = [{"id": 1, "value": "alpha"}, {"id": 2, "value": "beta"}]
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(data, f)
        json_path = f.name

    try:
        skill = JSONQuerySkill()
        result = asyncio.run(skill.execute({
            "data": json_path,
            "query": "0.value",
        }))
        assert result["success"] is True
        assert "alpha" in result["result"]
    finally:
        Path(json_path).unlink(missing_ok=True)


def test_extract_path_util():
    data = {"a": {"b": [10, 20, 30]}}
    assert _extract_path(data, "a.b[1]") == 20
    assert _extract_path(data, "a.b.2") == 30


def test_apply_filter_util():
    items = [{"name": "x", "val": 5}, {"name": "y", "val": 15}]
    result = _apply_filter(items, "val > 10")
    assert len(result) == 1
    assert result[0]["name"] == "y"


def test_apply_filter_contains():
    items = [
        {"name": "x", "tags": ["python", "ai"]},
        {"name": "y", "tags": ["rust"]},
    ]
    result = _apply_filter(items, "tags contains python")
    assert len(result) == 1
    assert result[0]["name"] == "x"


def test_aggregate_util():
    items = [{"v": 10}, {"v": 20}]
    assert _aggregate(items, "count") == 2
    assert _aggregate(items, "sum", "v") == 30.0
    assert _aggregate(items, "avg", "v") == 15.0


def test_format_as_table_empty():
    assert _format_as_table([]) == "(empty)"


# ══════════════════════════════════════════════════
# TranslateSkill
# ══════════════════════════════════════════════════

from mimir.skills.translate import TranslateSkill


def test_translate_properties():
    skill = TranslateSkill()
    assert skill.name == "translate"
    assert "translate_text" in skill.capabilities
    assert skill.risk_level == "safe"
    assert "text" in skill.param_schema


def test_translate_no_text():
    skill = TranslateSkill()
    result = asyncio.run(skill.execute({"text": ""}))
    assert result["success"] is False
    assert "no text" in result["error"].lower()


def test_translate_no_llm_client():
    skill = TranslateSkill(llm_client=None)
    result = asyncio.run(skill.execute({"text": "Hello", "target_language": "Chinese"}))
    assert result["success"] is False
    assert "llm client" in result["error"].lower()


def test_translate_mock_success():
    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock(return_value={"content": "你好世界"})

    skill = TranslateSkill(llm_client=mock_llm)
    result = asyncio.run(skill.execute({
        "text": "Hello World",
        "target_language": "Chinese",
    }))

    assert result["success"] is True
    assert "你好世界" in result["result"]


def test_translate_with_source_language():
    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock(return_value={"content": "Hello World"})

    skill = TranslateSkill(llm_client=mock_llm)
    result = asyncio.run(skill.execute({
        "text": "你好世界",
        "target_language": "English",
        "source_language": "Chinese",
    }))

    assert result["success"] is True
    assert "Hello World" in result["result"]
    # Verify the prompt included source language
    call_args = mock_llm.chat.call_args
    messages = call_args.kwargs.get("messages") or call_args[1].get("messages", [])
    prompt = messages[0]["content"]
    assert "Chinese" in prompt


def test_translate_llm_error():
    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock(side_effect=Exception("API timeout"))

    skill = TranslateSkill(llm_client=mock_llm)
    result = asyncio.run(skill.execute({
        "text": "Hello",
        "target_language": "Japanese",
    }))

    assert result["success"] is False
    assert "api timeout" in result["error"].lower()


# ══════════════════════════════════════════════════
# SummarizeURLSkill
# ══════════════════════════════════════════════════

from mimir.skills.summarize_url import SummarizeURLSkill


def test_summarize_url_properties():
    skill = SummarizeURLSkill()
    assert skill.name == "summarize_url"
    assert "summarize_webpage" in skill.capabilities
    assert skill.risk_level == "safe"
    assert "url" in skill.param_schema


def test_summarize_url_no_url():
    skill = SummarizeURLSkill()
    result = asyncio.run(skill.execute({}))
    assert result["success"] is False
    assert "no url" in result["error"].lower()


def test_summarize_url_no_llm():
    skill = SummarizeURLSkill(llm_client=None)
    result = asyncio.run(skill.execute({"url": "http://example.com"}))
    assert result["success"] is False
    assert "llm client" in result["error"].lower()


def test_summarize_url_mock_success():
    """Mock both httpx fetch and LLM call."""
    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock(return_value={
        "content": "This article discusses AI advancements in 2026."
    })

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.text = "<html><body><h1>AI in 2026</h1><p>Great advances in AI...</p></body></html>"
    mock_resp.headers = {}

    async def mock_get(*args, **kwargs):
        return mock_resp

    skill = SummarizeURLSkill(llm_client=mock_llm)

    with patch("mimir.skills.summarize_url.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = asyncio.run(skill.execute({
            "url": "http://example.com/article",
            "max_words": 50,
        }))

    assert result["success"] is True
    assert "AI" in result["result"]


def test_summarize_url_fetch_timeout():
    """When the HTTP fetch times out, should return a clean error."""
    mock_llm = AsyncMock()
    skill = SummarizeURLSkill(llm_client=mock_llm, timeout=1.0)

    import httpx as _httpx

    with patch("mimir.skills.summarize_url.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()

        async def mock_get(*args, **kwargs):
            raise _httpx.TimeoutException("timed out")

        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = asyncio.run(skill.execute({"url": "http://slow.example.com"}))

    assert result["success"] is False
    assert "timeout" in result["error"].lower()


# ══════════════════════════════════════════════════
# CustomToolManager
# ══════════════════════════════════════════════════

from mimir.skills.custom_tool import CustomToolManager, CustomToolDefinition, _CustomSkillWrapper


def test_custom_tool_definition_validate():
    defn = CustomToolDefinition(
        name="my_tool",
        description="Test",
        capabilities=["test"],
        risk_level="safe",
        tool_type="api",
        config={"url": "http://example.com"},
    )
    assert defn.validate() is None


def test_custom_tool_definition_invalid_name():
    defn = CustomToolDefinition(
        name="BAD NAME!",
        description="Test",
        capabilities=[],
        risk_level="safe",
        tool_type="api",
        config={"url": "http://example.com"},
    )
    assert defn.validate() is not None


def test_custom_tool_shell_forced_dangerous():
    defn = CustomToolDefinition(
        name="my_shell",
        description="Test",
        capabilities=[],
        risk_level="safe",  # Should be forced to dangerous
        tool_type="shell",
        config={"command_template": "echo hello"},
    )
    defn.validate()
    assert defn.risk_level == "dangerous"


def test_custom_tool_manager_register_and_list():
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = CustomToolManager(tools_dir=tmpdir)

        name = mgr.register_tool({
            "name": "check_btc",
            "description": "Check BTC price",
            "capabilities": ["crypto_price"],
            "risk_level": "safe",
            "tool_type": "api",
            "config": {
                "url": "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
                "method": "GET",
                "extract_path": "bitcoin.usd",
            },
        })

        assert name == "check_btc"
        tools = mgr.list_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "custom:check_btc"

        # Verify file was written
        assert (Path(tmpdir) / "check_btc.json").exists()


def test_custom_tool_manager_remove():
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = CustomToolManager(tools_dir=tmpdir)
        mgr.register_tool({
            "name": "temp_tool",
            "description": "Temp",
            "capabilities": [],
            "risk_level": "safe",
            "tool_type": "api",
            "config": {"url": "http://example.com"},
        })

        assert mgr.remove_tool("temp_tool") is True
        assert mgr.list_tools() == []
        assert not (Path(tmpdir) / "temp_tool.json").exists()


def test_custom_tool_manager_remove_nonexistent():
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = CustomToolManager(tools_dir=tmpdir)
        assert mgr.remove_tool("nonexistent") is False


def test_custom_tool_manager_load_from_disk():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Write a JSON file directly
        tool_def = {
            "name": "disk_tool",
            "description": "Loaded from disk",
            "capabilities": ["test"],
            "risk_level": "safe",
            "tool_type": "api",
            "config": {"url": "http://example.com", "method": "GET"},
        }
        (Path(tmpdir) / "disk_tool.json").write_text(
            json.dumps(tool_def), encoding="utf-8"
        )

        mgr = CustomToolManager(tools_dir=tmpdir)
        skills = mgr.load_tools()
        assert len(skills) == 1
        assert skills[0].name == "custom:disk_tool"


def test_custom_tool_manager_invalid_definition_skipped():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Write an invalid JSON file (bad name)
        (Path(tmpdir) / "bad.json").write_text(
            json.dumps({"name": "", "tool_type": "api", "config": {}}),
            encoding="utf-8",
        )

        mgr = CustomToolManager(tools_dir=tmpdir)
        skills = mgr.load_tools()
        assert len(skills) == 0


def test_custom_tool_api_execution():
    """Test _CustomSkillWrapper with an API tool."""
    defn = CustomToolDefinition(
        name="test_api",
        description="Test API",
        capabilities=["test"],
        risk_level="safe",
        tool_type="api",
        config={
            "url": "https://api.example.com/data",
            "method": "GET",
            "extract_path": "result.value",
        },
    )
    wrapper = _CustomSkillWrapper(defn)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"result": {"value": 42}}

    async def mock_get(*args, **kwargs):
        return mock_resp

    with patch("mimir.skills.custom_tool.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = asyncio.run(wrapper.execute({}))

    assert result["success"] is True
    assert "42" in result["result"]


def test_custom_tool_transform_execution():
    """Test _CustomSkillWrapper with a transform tool."""
    defn = CustomToolDefinition(
        name="test_transform",
        description="Extract data field",
        capabilities=["transform"],
        risk_level="safe",
        tool_type="transform",
        config={
            "jq_expression": ".items",
            "output_format": "text",
        },
    )
    wrapper = _CustomSkillWrapper(defn)

    input_data = json.dumps({"items": ["a", "b", "c"]})
    result = asyncio.run(wrapper.execute({"input_data": input_data}))

    assert result["success"] is True
    assert "a" in result["result"]
    assert "b" in result["result"]
    assert "c" in result["result"]
