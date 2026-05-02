"""
Pytest configuration and shared fixtures for image-gen tests.
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient  # noqa: E402

# Ensure project's code/ directory is on sys.path so that `app.*` imports work
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CODE_DIR = PROJECT_ROOT / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))


@pytest.fixture
def client():
    """Provide a TestClient for the FastAPI app without starting real servers."""
    # Patch MCP server initialisation to avoid blocking / real network calls
    from app import server as server_module

    # We need a fresh app instance — just use the module-level `app`
    # but ensure MCP isn't started. The module-level code already creates
    # the `mcp` instance but only runs it inside `main()`, so TestClient
    # is safe to use here.
    with TestClient(server_module.app) as tc:
        yield tc