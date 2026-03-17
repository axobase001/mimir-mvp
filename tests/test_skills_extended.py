"""Tests for Step 5 new skills."""

import asyncio
import io
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from mimir.skills.base import Skill, SkillResult
from mimir.skills.code_exec import CodeExecSkill
from mimir.skills.document import DocumentSkill
from mimir.skills.email_skill import EmailSkill
from mimir.skills.web_fetch import WebFetchSkill
from mimir.skills.data_analysis import DataAnalysisSkill
from mimir.skills.openclaw_adapter import OpenClawAdapter, WrappedOpenClawSkill


# ── SkillResult ──

def test_skill_result_defaults():
    r = SkillResult(success=True)
    assert r.success is True
    assert r.result == ""
    assert r.error is None
    assert r.artifacts == []
    assert r.summary == ""
    assert r.pe_impact == 0.0


def test_skill_result_full():
    r = SkillResult(
        success=True,
        result="hello",
        error=None,
        artifacts=["file.txt"],
        summary="done",
        pe_impact=-0.05,
    )
    assert r.result == "hello"
    assert r.artifacts == ["file.txt"]
    assert r.pe_impact == -0.05


# ── Risk levels ──

def test_risk_levels():
    assert CodeExecSkill().risk_level == "dangerous"
    assert EmailSkill().risk_level == "dangerous"
    assert DocumentSkill().risk_level == "review"
    assert WebFetchSkill().risk_level == "safe"
    assert DataAnalysisSkill().risk_level == "safe"


# ── Capabilities ──

def test_capabilities():
    assert "run_code" in CodeExecSkill().capabilities
    assert "send_email" in EmailSkill().capabilities
    assert "write_document" in DocumentSkill().capabilities
    assert "fetch_url" in WebFetchSkill().capabilities
    assert "analyze_data" in DataAnalysisSkill().capabilities


# ── CodeExecSkill ──

def test_code_exec_simple():
    skill = CodeExecSkill(timeout=10)
    result = asyncio.run(skill.execute({"code": "print('hello world')"}))
    assert result["success"] is True
    assert "hello world" in result["result"]


def test_code_exec_error():
    skill = CodeExecSkill(timeout=10)
    result = asyncio.run(skill.execute({"code": "raise ValueError('boom')"}))
    assert result["success"] is False
    assert "boom" in result["error"]


def test_code_exec_empty():
    skill = CodeExecSkill()
    result = asyncio.run(skill.execute({"code": ""}))
    assert result["success"] is False
    assert "Empty" in result["error"]


def test_code_exec_timeout():
    skill = CodeExecSkill(timeout=2)
    result = asyncio.run(skill.execute({
        "code": "import time; time.sleep(10)",
        "timeout": 2,
    }))
    assert result["success"] is False
    assert "timed out" in result["error"].lower() or "timeout" in result["error"].lower()


# ── DocumentSkill ──

def test_document_create():
    with tempfile.TemporaryDirectory() as tmpdir:
        skill = DocumentSkill(workspace=tmpdir)
        result = asyncio.run(skill.execute({
            "action": "create",
            "filename": "test.md",
            "content": "Hello world",
            "title": "Test Doc",
        }))
        assert result["success"] is True
        content = (Path(tmpdir) / "test.md").read_text(encoding="utf-8")
        assert "# Test Doc" in content
        assert "Hello world" in content


def test_document_append():
    with tempfile.TemporaryDirectory() as tmpdir:
        skill = DocumentSkill(workspace=tmpdir)
        asyncio.run(skill.execute({
            "action": "create",
            "filename": "test.txt",
            "content": "line1",
        }))
        asyncio.run(skill.execute({
            "action": "append",
            "filename": "test.txt",
            "content": "line2",
        }))
        content = (Path(tmpdir) / "test.txt").read_text(encoding="utf-8")
        assert "line1" in content
        assert "line2" in content


def test_document_edit():
    with tempfile.TemporaryDirectory() as tmpdir:
        skill = DocumentSkill(workspace=tmpdir)
        asyncio.run(skill.execute({
            "action": "create",
            "filename": "test.txt",
            "content": "original",
        }))
        result = asyncio.run(skill.execute({
            "action": "edit",
            "filename": "test.txt",
            "content": "replaced",
        }))
        assert result["success"] is True
        assert (Path(tmpdir) / "test.txt").read_text(encoding="utf-8") == "replaced"


def test_document_no_filename():
    skill = DocumentSkill()
    result = asyncio.run(skill.execute({"action": "create", "filename": "", "content": "x"}))
    assert result["success"] is False


# ── EmailSkill ──

def test_email_missing_recipient():
    skill = EmailSkill()
    result = asyncio.run(skill.execute({"to": "", "subject": "hi", "body": "test"}))
    assert result["success"] is False
    assert "recipient" in result["error"].lower()


def test_email_missing_subject():
    skill = EmailSkill()
    result = asyncio.run(skill.execute({"to": "a@b.com", "subject": "", "body": "test"}))
    assert result["success"] is False
    assert "subject" in result["error"].lower()


def test_email_mock_send():
    skill = EmailSkill(smtp_host="localhost", smtp_port=587, smtp_user="u", smtp_pass="p")

    with patch("mimir.skills.email_skill.aiosmtplib") as mock_smtp:
        mock_smtp.send = AsyncMock(return_value=({}, "OK"))
        result = asyncio.run(skill.execute({
            "to": "test@example.com",
            "subject": "Test",
            "body": "Hello",
        }))

    assert result["success"] is True
    assert "test@example.com" in result["result"]


# ── WebFetchSkill ──

def test_web_fetch_no_url():
    skill = WebFetchSkill()
    result = asyncio.run(skill.execute({"url": ""}))
    assert result["success"] is False


def test_web_fetch_mock():
    skill = WebFetchSkill()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.text = "<html><body><h1>Title</h1><p>Content here</p></body></html>"
    mock_resp.headers = {}

    async def mock_get(*args, **kwargs):
        return mock_resp

    with patch("mimir.skills.web_fetch.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = asyncio.run(skill.execute({"url": "https://example.com"}))

    assert result["success"] is True
    assert "Title" in result["result"]
    assert "Content here" in result["result"]


# ── DataAnalysisSkill ──

def test_data_analysis_summary_csv():
    csv_data = "a,b,c\n1,2,3\n4,5,6\n7,8,9"
    skill = DataAnalysisSkill()
    result = asyncio.run(skill.execute({
        "source": csv_data,
        "analysis_type": "summary",
    }))
    assert result["success"] is True
    assert "3 rows" in result["result"]
    assert "a" in result["result"]


def test_data_analysis_correlation():
    csv_data = "x,y\n1,2\n2,4\n3,6\n4,8"
    skill = DataAnalysisSkill()
    result = asyncio.run(skill.execute({
        "source": csv_data,
        "analysis_type": "correlation",
    }))
    assert result["success"] is True
    assert "Correlation" in result["result"]


def test_data_analysis_trend():
    csv_data = "val\n10\n20\n30\n40\n50\n60"
    skill = DataAnalysisSkill()
    result = asyncio.run(skill.execute({
        "source": csv_data,
        "analysis_type": "trend",
        "column": "val",
    }))
    assert result["success"] is True
    assert "UPWARD" in result["result"]


def test_data_analysis_json():
    json_data = '[{"a": 1, "b": 2}, {"a": 3, "b": 4}]'
    skill = DataAnalysisSkill()
    result = asyncio.run(skill.execute({
        "source": json_data,
        "analysis_type": "summary",
    }))
    assert result["success"] is True
    assert "2 rows" in result["result"]


def test_data_analysis_custom():
    csv_data = "x,y\n1,10\n2,20\n3,30"
    skill = DataAnalysisSkill()
    result = asyncio.run(skill.execute({
        "source": csv_data,
        "analysis_type": "custom",
        "query": "df['y'].sum()",
    }))
    assert result["success"] is True
    assert "60" in result["result"]


def test_data_analysis_file():
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8"
    ) as f:
        f.write("col1,col2\n1,2\n3,4\n5,6\n")
        tmp_path = f.name

    try:
        skill = DataAnalysisSkill()
        result = asyncio.run(skill.execute({
            "source": tmp_path,
            "analysis_type": "summary",
        }))
        assert result["success"] is True
        assert "3 rows" in result["result"]
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def test_data_analysis_no_source():
    skill = DataAnalysisSkill()
    result = asyncio.run(skill.execute({"source": ""}))
    assert result["success"] is False


# ── OpenClawAdapter ──

def test_openclaw_load_empty():
    adapter = OpenClawAdapter(skill_dirs=[])
    skills = adapter.load_skills()
    assert skills == []


def test_openclaw_load_from_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a mock OpenClaw skill module
        skill_file = Path(tmpdir) / "greet.py"
        skill_file.write_text(
            'SKILL_NAME = "greet"\n'
            'SKILL_DESCRIPTION = "Greet someone"\n'
            'SKILL_CAPABILITIES = ["greet", "hello"]\n'
            'SKILL_RISK = "safe"\n'
            'SKILL_PARAMS = {"name": {"type": "str", "required": True}}\n'
            '\n'
            'def execute(params):\n'
            '    return {"success": True, "result": f"Hello {params.get(\'name\', \'world\')}!", "error": None}\n',
            encoding="utf-8",
        )

        adapter = OpenClawAdapter(skill_dirs=[tmpdir])
        skills = adapter.load_skills()
        assert len(skills) == 1
        assert skills[0].name == "greet"
        assert skills[0].risk_level == "safe"
        assert "greet" in skills[0].capabilities

        # Test execution
        result = asyncio.run(skills[0].execute({"name": "Wren"}))
        assert result["success"] is True
        assert "Hello Wren!" in result["result"]


def test_openclaw_register_all():
    from mimir.skills.registry import SmartSkillRegistry

    with tempfile.TemporaryDirectory() as tmpdir:
        skill_file = Path(tmpdir) / "calc.py"
        skill_file.write_text(
            'SKILL_NAME = "calc"\n'
            'SKILL_DESCRIPTION = "Calculate"\n'
            'SKILL_CAPABILITIES = ["calculate"]\n'
            '\n'
            'def execute(params):\n'
            '    return {"success": True, "result": "42", "error": None}\n',
            encoding="utf-8",
        )

        adapter = OpenClawAdapter(skill_dirs=[tmpdir])
        registry = SmartSkillRegistry()
        count = adapter.register_all(registry)
        assert count == 1
        assert registry.get("calc") is not None


# ── SEC tracking on Skill base ──

def test_skill_sec_tracking():
    skill = CodeExecSkill()
    assert skill.success_rate == 0.0
    assert skill.avg_pe_improvement == 0.0

    skill.record_outcome(True, 0.5, 0.3)
    skill.record_outcome(True, 0.4, 0.2)
    skill.record_outcome(False, 0.6, 0.6)

    assert skill.success_rate == 2 / 3
    # avg improvement: (0.2 + 0.2 + 0.0) / 3 = 0.1333...
    assert abs(skill.avg_pe_improvement - (0.2 + 0.2 + 0.0) / 3) < 0.001
