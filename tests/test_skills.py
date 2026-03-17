import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from mimir.skills.base import SkillRegistry
from mimir.skills.search import BraveSearchSkill
from mimir.skills.file_io import FileReadSkill, FileWriteSkill


def test_skill_registry():
    registry = SkillRegistry()
    registry.register(FileReadSkill())
    registry.register(FileWriteSkill())

    assert len(registry.list_skills()) == 2
    assert registry.get("file_read") is not None
    assert registry.get("nonexistent") is None


def test_brave_search_mock():
    skill = BraveSearchSkill(api_key="test")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "web": {
            "results": [
                {"title": "GDPR Fines 2024", "description": "Fines reached $1.2M"},
                {"title": "EU Data Protection", "description": "New regulations..."},
            ]
        }
    }

    async def mock_get(*args, **kwargs):
        return mock_resp

    with patch("mimir.skills.search.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        result = asyncio.run(skill.execute({"query": "GDPR fines 2024"}))

    assert result["success"] is True
    assert "GDPR" in result["result"]


def test_file_read_write():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = str(Path(tmpdir) / "test.txt")

        write_skill = FileWriteSkill()
        result = asyncio.run(write_skill.execute({
            "path": path, "content": "hello world", "mode": "w"
        }))
        assert result["success"] is True

        read_skill = FileReadSkill()
        result = asyncio.run(read_skill.execute({"path": path}))
        assert result["success"] is True
        assert result["result"] == "hello world"


def test_file_read_nonexistent():
    read_skill = FileReadSkill()
    result = asyncio.run(read_skill.execute({"path": "/nonexistent/path.txt"}))
    assert result["success"] is False


def test_file_append():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = str(Path(tmpdir) / "test.txt")

        write_skill = FileWriteSkill()
        asyncio.run(write_skill.execute({"path": path, "content": "line1\n"}))
        asyncio.run(write_skill.execute({"path": path, "content": "line2\n", "mode": "a"}))

        read_skill = FileReadSkill()
        result = asyncio.run(read_skill.execute({"path": path}))
        assert result["result"] == "line1\nline2\n"
