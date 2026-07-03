import importlib
import os
from unittest import TestCase
from unittest.mock import patch


class McpConfigTests(TestCase):
    def test_default_mcp_url_is_local(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = importlib.import_module("digital_brain.config")
            self.assertEqual(config.get_mcp_url(), "http://localhost:8000/api/mcp/")

    def test_mcp_url_can_be_overridden(self) -> None:
        with patch.dict(os.environ, {"DIGITAL_BRAIN_MCP_URL": "http://127.0.0.1:9999/api/mcp/"}):
            config = importlib.import_module("digital_brain.config")
            self.assertEqual(config.get_mcp_url(), "http://127.0.0.1:9999/api/mcp/")
