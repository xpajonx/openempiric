from __future__ import annotations
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from oem_knowledge.engine import KnowledgeEngine
from oem_knowledge.adapters import BaseAdapter, register_adapter, get_adapter, get_registered_adapter

def test_custom_adapter_registration():
    """Verify that custom adapters can be dynamically registered and instantiated."""
    
    # 1. Define custom adapter decorated with register_adapter
    @register_adapter("mock-custom-agent")
    class MockCustomAdapter(BaseAdapter):
        def parse_transcript(self, transcript_path: Path) -> str:
            return "Custom Parsed: " + transcript_path.name

    # 2. Get registered class directly
    cls = get_registered_adapter("mock-custom-agent")
    assert cls is MockCustomAdapter

    # 3. Resolve through engine get_adapter
    mock_engine = MagicMock(spec=KnowledgeEngine)
    adapter = get_adapter("mock-custom-agent", mock_engine)
    
    assert isinstance(adapter, MockCustomAdapter)
    
    # 4. Check custom functionality
    dummy_path = Path("chat_test.md")
    assert adapter.parse_transcript(dummy_path) == "Custom Parsed: chat_test.md"

def test_adapter_sdk_defaults():
    """Verify that default hook implementations in BaseAdapter don't raise errors."""
    mock_engine = MagicMock(spec=KnowledgeEngine)
    adapter = BaseAdapter(mock_engine)
    
    # Defaults should return False or empty/none as specified in base class
    assert adapter.install_skill() is False
    assert adapter.verify_mcp() is False
    assert adapter.discover_latest_transcript() is None
    
    # Verify no-op hook invocations do not raise exceptions
    try:
        adapter.pre_session()
        adapter.post_session(committed=True)
    except Exception as e:
        pytest.fail(f"Hooks raised exception unexpectedly: {e}")
