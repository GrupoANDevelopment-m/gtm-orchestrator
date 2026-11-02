"""
conftest.py — pytest fixtures for orchestrator test suite.

Strategy: tests target the running server via HTTP (httpx). The server must be
running on PORT (default 8002). Each test asserts HTTP status + JSON shape.

For unit tests (text sanitization, etc.) we import the module directly.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx
import pytest

# Make server/ importable
SERVER_DIR = Path(__file__).resolve().parent.parent / "server"
sys.path.insert(0, str(SERVER_DIR))

# Default config
DEFAULT_PORT = int(os.environ.get("PORT", "8002"))
DEFAULT_BASE = f"http://127.0.0.1:{DEFAULT_PORT}"
API_KEY_PATH = SERVER_DIR.parent / "keys" / ".orchestrator_api_key.txt"


def _wait_for_server(base: str, timeout: float = 30.0) -> bool:
    """Block until /health returns 200, or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base}/health", timeout=2.0)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _load_api_key() -> str:
    """Read the API key from disk."""
    try:
        return API_KEY_PATH.read_text().strip()
    except Exception:
        return ""


@pytest.fixture(scope="session")
def base_url() -> str:
    """Return the server base URL (default http://127.0.0.1:8002)."""
    return DEFAULT_BASE


@pytest.fixture(scope="session")
def api_key() -> str:
    """Return the orchestrator API key."""
    return _load_api_key()


@pytest.fixture(scope="session")
def client(base_url, api_key):
    """Provide an httpx.Client with the API key header pre-set."""
    if not _wait_for_server(base_url, timeout=20.0):
        pytest.skip(f"Server not reachable at {base_url}")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    with httpx.Client(base_url=base_url, headers=headers, timeout=60.0) as c:
        yield c


@pytest.fixture(scope="session")
def async_client(base_url, api_key):
    """Async client for SSE / long-poll tests."""
    if not _wait_for_server(base_url, timeout=20.0):
        pytest.skip(f"Server not reachable at {base_url}")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    return httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30.0)
