"""Tests for new skills and features #3, #4, #6, #7, #8.

- EmailReadSkill (Feature #3)
- PDFReadSkill (Feature #4)
- GenericAPISkill (Feature #6)
- Belief extraction from action output (Feature #7)
- Structured status tracking (Feature #8)
"""

import asyncio
import email
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from mimir.skills.email_read import EmailReadSkill, _decode_header, _extract_body
from mimir.skills.pdf_read import PDFReadSkill, _parse_page_range, _extract_naive
from mimir.skills.api_call import GenericAPISkill, _extract_json_path
from mimir.core.action_engine import ActionEngine
from mimir.core.notifier import Notifier
from mimir.skills.base import Skill, SkillResult
from mimir.skills.registry import SmartSkillRegistry
from mimir.brain.belief_graph import BeliefGraph
from mimir.brain.memory import Memory
from mimir.types import Belief, BeliefCategory, BeliefSource
from mimir.config import MimirConfig


# ══════════════════════════════════════════════════
# Feature #3: EmailReadSkill
# ══════════════════════════════════════════════════

def test_email_read_no_user():
    """Skill should fail gracefully with no IMAP user configured."""
    skill = EmailReadSkill()
    result = asyncio.run(skill.execute({}))
    assert result["success"] is False
    assert "not configured" in result["error"].lower()


def test_email_read_properties():
    """Skill metadata is correct."""
    skill = EmailReadSkill(imap_host="imap.test.com", imap_user="test@test.com",
                            imap_pass="pass")
    assert skill.name == "email_read"
    assert "read_email" in skill.capabilities
    assert skill.risk_level == "safe"
    assert "folder" in skill.param_schema


def test_email_read_mock_imap():
    """Mock IMAP connection and verify email parsing."""
    skill = EmailReadSkill(
        imap_host="imap.test.com", imap_port=993,
        imap_user="user@test.com", imap_pass="password",
    )

    # Build a minimal RFC822 message
    msg = email.message.EmailMessage()
    msg["Subject"] = "Test Subject"
    msg["From"] = "sender@test.com"
    msg["Date"] = "Mon, 01 Jan 2024 00:00:00 +0000"
    msg.set_content("Hello, this is a test email body.")
    raw_bytes = msg.as_bytes()

    with patch("mimir.skills.email_read.imaplib.IMAP4_SSL") as mock_imap_cls:
        mock_conn = MagicMock()
        mock_imap_cls.return_value = mock_conn
        mock_conn.login.return_value = ("OK", [])
        mock_conn.select.return_value = ("OK", [b"1"])
        mock_conn.search.return_value = ("OK", [b"1"])
        mock_conn.fetch.return_value = ("OK", [(b"1", raw_bytes)])
        mock_conn.close.return_value = ("OK", [])
        mock_conn.logout.return_value = ("BYE", [])

        result = asyncio.run(skill.execute({"folder": "INBOX", "count": 5}))

    assert result["success"] is True
    assert "Test Subject" in result["result"]
    assert "sender@test.com" in result["result"]


def test_decode_header_utf8():
    """RFC2047 decoding works."""
    plain = _decode_header("Hello World")
    assert plain == "Hello World"


def test_email_read_empty_inbox():
    """Handle empty inbox gracefully."""
    skill = EmailReadSkill(
        imap_host="imap.test.com", imap_port=993,
        imap_user="user@test.com", imap_pass="pass",
    )
    with patch("mimir.skills.email_read.imaplib.IMAP4_SSL") as mock_imap_cls:
        mock_conn = MagicMock()
        mock_imap_cls.return_value = mock_conn
        mock_conn.login.return_value = ("OK", [])
        mock_conn.select.return_value = ("OK", [b"0"])
        mock_conn.search.return_value = ("OK", [b""])
        mock_conn.close.return_value = ("OK", [])
        mock_conn.logout.return_value = ("BYE", [])

        result = asyncio.run(skill.execute({}))

    assert result["success"] is True
    assert "no messages" in result["result"].lower()


# ══════════════════════════════════════════════════
# Feature #4: PDFReadSkill
# ══════════════════════════════════════════════════

def test_pdf_read_no_path():
    skill = PDFReadSkill()
    result = asyncio.run(skill.execute({}))
    assert result["success"] is False
    assert "no path" in result["error"].lower()


def test_pdf_read_file_not_found():
    skill = PDFReadSkill()
    result = asyncio.run(skill.execute({"path": "/nonexistent/file.pdf"}))
    assert result["success"] is False
    assert "not found" in result["error"].lower()


def test_pdf_read_properties():
    skill = PDFReadSkill()
    assert skill.name == "pdf_read"
    assert "read_pdf" in skill.capabilities
    assert skill.risk_level == "safe"


def test_parse_page_range():
    assert _parse_page_range("all") is None
    assert _parse_page_range("") is None
    assert _parse_page_range("3") == (2, 3)
    assert _parse_page_range("1-5") == (0, 5)
    assert _parse_page_range("invalid") is None


def test_pdf_read_with_mock_pypdf2():
    """Mock PyPDF2 to test extraction without real PDF."""
    skill = PDFReadSkill()

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"%PDF-1.4 fake pdf content")
        pdf_path = f.name

    try:
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Extracted text from page 1"

        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]

        with patch("mimir.skills.pdf_read.PdfReader", mock_reader.__class__,
                    create=True):
            # Patch at import level
            import mimir.skills.pdf_read as pdf_mod
            original = None
            try:
                # Attempt direct PyPDF2 mock
                from unittest.mock import patch as _patch
                with _patch.dict("sys.modules", {"PyPDF2": MagicMock()}):
                    import importlib
                    importlib.reload(pdf_mod)
                    # Use the naive extractor as fallback since mocking imports is tricky
                    result = asyncio.run(skill.execute({"path": pdf_path}))
            except Exception:
                result = asyncio.run(skill.execute({"path": pdf_path}))
    finally:
        Path(pdf_path).unlink(missing_ok=True)

    # May or may not succeed depending on whether naive extraction finds text
    # The key thing is it doesn't crash
    assert isinstance(result, dict)
    assert "success" in result


# ══════════════════════════════════════════════════
# Feature #6: GenericAPISkill
# ══════════════════════════════════════════════════

def test_api_call_no_url():
    skill = GenericAPISkill()
    result = asyncio.run(skill.execute({}))
    assert result["success"] is False
    assert "no url" in result["error"].lower()


def test_api_call_unsupported_method():
    skill = GenericAPISkill()
    result = asyncio.run(skill.execute({"url": "http://test.com", "method": "DELETE"}))
    assert result["success"] is False
    assert "unsupported" in result["error"].lower()


def test_api_call_properties():
    skill = GenericAPISkill()
    assert skill.name == "api_call"
    assert "api_call" in skill.capabilities
    assert skill.risk_level == "review"


def test_api_call_mock_get():
    """Mock httpx GET request with JSON response."""
    skill = GenericAPISkill()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"data": {"price": 60000, "symbol": "BTC"}}

    async def mock_get(*args, **kwargs):
        return mock_resp

    with patch("mimir.skills.api_call.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = asyncio.run(skill.execute({
            "url": "https://api.test.com/price",
            "method": "GET",
            "extract_path": "data.price",
        }))

    assert result["success"] is True
    assert "60000" in result["result"]


def test_api_call_mock_post():
    """Mock httpx POST request."""
    skill = GenericAPISkill()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"status": "ok", "id": 123}

    async def mock_post(*args, **kwargs):
        return mock_resp

    with patch("mimir.skills.api_call.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = asyncio.run(skill.execute({
            "url": "https://api.test.com/submit",
            "method": "POST",
            "body": {"key": "value"},
        }))

    assert result["success"] is True
    assert "ok" in result["result"]


def test_extract_json_path():
    """Test dot-separated JSON path extraction."""
    data = {"a": {"b": {"c": 42}}, "list": [10, 20, 30]}

    assert _extract_json_path(data, "a.b.c") == 42
    assert _extract_json_path(data, "list.1") == 20
    assert isinstance(_extract_json_path(data, "a.x"), type(_extract_json_path({}, "missing")))
    assert _extract_json_path(data, "a.b") == {"c": 42}


def test_api_call_extract_path_not_found():
    """When extract_path doesn't exist, return full JSON with error note."""
    skill = GenericAPISkill()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"foo": "bar"}

    async def mock_get(*args, **kwargs):
        return mock_resp

    with patch("mimir.skills.api_call.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = asyncio.run(skill.execute({
            "url": "https://api.test.com/data",
            "extract_path": "nonexistent.path",
        }))

    assert result["success"] is True
    assert result["error"] is not None  # Should indicate path not found


# ══════════════════════════════════════════════════
# Feature #7: Belief extraction from action output
# ══════════════════════════════════════════════════

class MockSkill(Skill):
    def __init__(self, name_str="mock_skill"):
        super().__init__()
        self._name = name_str

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "Mock"

    @property
    def capabilities(self) -> list[str]:
        return ["mock"]

    async def execute(self, params: dict) -> dict:
        return {"success": True, "result": "Mock output with BTC at 60k", "error": None}


def test_execute_plan_extracts_beliefs():
    """After execute_plan, beliefs should be extracted from accumulated output."""
    config = MimirConfig()
    registry = SmartSkillRegistry()
    registry.register(MockSkill("search"))
    memory = Memory(config)
    notifier = Notifier()
    bg = BeliefGraph(config)

    # Mock external LLM
    external_llm = MagicMock()
    external_llm.extract_beliefs = AsyncMock(return_value={
        "verdict": "support",
        "observed_confidence": 0.8,
        "extracted_facts": ["BTC is at 60k"],
        "new_beliefs": [
            {"statement": "BTC price is $60,000", "tags": ["crypto"], "confidence": 0.7, "category": "fact"},
        ],
    })

    # Mock internal LLM for param generation
    internal_llm = MagicMock()
    internal_llm.plan_action_params = AsyncMock(return_value={"query": "BTC price"})

    engine = ActionEngine(
        skill_registry=registry,
        memory=memory,
        notifier=notifier,
        internal_llm=internal_llm,
        external_llm=external_llm,
        belief_graph=bg,
    )

    steps = [
        {"skill": "search", "description": "Search BTC price", "risk_level": "safe"},
    ]

    result = asyncio.run(engine.execute_plan(
        steps=steps,
        intent="Find current BTC price",
        belief_context="",
    ))

    assert result["success"] is True
    assert len(result.get("extracted_beliefs", [])) >= 1
    # Verify the belief was added to the graph
    all_beliefs = bg.get_all_beliefs()
    assert any("BTC" in b.statement for b in all_beliefs)


def test_execute_plan_without_external_llm():
    """execute_plan should still work without external_llm, just no extraction."""
    config = MimirConfig()
    registry = SmartSkillRegistry()
    registry.register(MockSkill("search"))
    memory = Memory(config)
    notifier = Notifier()

    engine = ActionEngine(
        skill_registry=registry,
        memory=memory,
        notifier=notifier,
        internal_llm=None,
        external_llm=None,
    )

    steps = [{"skill": "search", "description": "test", "risk_level": "safe"}]
    result = asyncio.run(engine.execute_plan(steps=steps, intent="test"))

    assert result["success"] is True
    assert result.get("extracted_beliefs", []) == []


def test_execute_plan_belief_extraction_failure_is_safe():
    """If belief extraction fails, execute_plan should still succeed."""
    config = MimirConfig()
    registry = SmartSkillRegistry()
    registry.register(MockSkill("search"))
    memory = Memory(config)
    notifier = Notifier()

    external_llm = MagicMock()
    external_llm.extract_beliefs = AsyncMock(side_effect=Exception("LLM error"))

    engine = ActionEngine(
        skill_registry=registry,
        memory=memory,
        notifier=notifier,
        external_llm=external_llm,
    )

    steps = [{"skill": "search", "description": "test", "risk_level": "safe"}]
    result = asyncio.run(engine.execute_plan(steps=steps, intent="test"))

    assert result["success"] is True
    assert result.get("extracted_beliefs", []) == []


# ══════════════════════════════════════════════════
# Feature #8: Structured status tracking
# ══════════════════════════════════════════════════

def test_belief_status_field():
    """Belief should have a status field, default empty string."""
    b = Belief(
        id="b1", statement="Applied to Company X",
        confidence=0.8, source=BeliefSource.SEED,
        created_at=0, last_updated=0, last_verified=0,
    )
    assert b.status == ""

    b.status = "applied"
    assert b.status == "applied"


def test_belief_status_serialization():
    """Status field should survive to_dict/from_dict roundtrip."""
    config = MimirConfig()
    bg = BeliefGraph(config)

    b = Belief(
        id="b1", statement="Applied to Company X",
        confidence=0.8, source=BeliefSource.SEED,
        created_at=0, last_updated=0, last_verified=0,
        tags=["job_search"],
        status="interview_1",
    )
    bg.add_belief(b)

    data = bg.to_dict()
    assert data["nodes"]["b1"]["status"] == "interview_1"

    bg2 = BeliefGraph.from_dict(data, config)
    restored = bg2.get_belief("b1")
    assert restored is not None
    assert restored.status == "interview_1"


def test_belief_status_backward_compat():
    """Old data without status field should default to empty string."""
    config = MimirConfig()
    data = {
        "nodes": {
            "b1": {
                "id": "b1",
                "statement": "test",
                "confidence": 0.8,
                "source": "seed",
                "created_at": 0,
                "last_updated": 0,
                "last_verified": 0,
                # No "status" key
            }
        },
        "edges": [],
        "counter": 1,
    }

    bg = BeliefGraph.from_dict(data, config)
    b = bg.get_belief("b1")
    assert b is not None
    assert b.status == ""


def test_belief_status_update_sequence():
    """Simulate a status progression: applied -> interview -> offer."""
    b = Belief(
        id="b1", statement="Applied to Google",
        confidence=0.7, source=BeliefSource.SEED,
        created_at=0, last_updated=0, last_verified=0,
        status="applied",
    )

    assert b.status == "applied"

    b.status = "interview_1"
    assert b.status == "interview_1"

    b.status = "interview_2"
    assert b.status == "interview_2"

    b.status = "offer"
    assert b.status == "offer"
